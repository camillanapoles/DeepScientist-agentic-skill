"""Internal self-validating agent for merge-readiness.

This is the *agent inside* the event-driven system. It does not call an
external LLM to decide success — it **imperatively runs checks** and
feeds their results back through the deterministic state machine until
the object reaches ``state=ready`` (or ``state=failed`` after the retry
budget is exhausted).

Each check is a pure callable returning a
:class:`ValidationCheckResult`. Results are:

1. persisted to the DB as ``validation.check_result`` events,
2. translated into ``validation.result`` for the engine,
3. re-evaluated on the next loop iteration if anything failed.

This is the OSWorld 2.0 long-horizon pattern: *read state → run one
atomic verification → record typed result → repeat until success*, with
an explicit attempt cap to prevent infinite loops.

The agent can also run arbitrary check functions (injected by callers
or tests), so CI/CD can register project-specific validators.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Callable, Iterable

from ..shared import generate_id, utc_now
from .models import (
    CHECK_AUDIT_LINUX,
    CHECK_AUDIT_WINDOWS,
    CHECK_COMPILE,
    CHECK_NO_TEMPLATE_ENGINE,
    CHECK_PLAYBOOK_RENDER,
    CHECK_UNIT_TESTS,
    DEFAULT_MERGE_CHECKS,
    EVT_MERGE_BLOCKED,
    EVT_MERGE_READY,
    EVT_VALIDATION_CHECK_REQUESTED,
    EVT_VALIDATION_CHECK_RESULT,
    EVT_VALIDATION_RESULT,
    EVT_VALIDATION_RETRY,
    STATE_BLOCKED_HUMAN,
    STATE_FAILED,
    STATE_READY,
    STATE_VALIDATING,
    KIND_MERGE_GATE,
    ValidationCheckResult,
)
from .schemas import ClosedLoopVerdict

logger = logging.getLogger(__name__)

CheckFn = Callable[[], ValidationCheckResult]


def _run_subprocess(args: list[str], cwd: Path, timeout: int = 180) -> tuple[int, str, str, int]:
    import subprocess

    start = time.monotonic()
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        duration = int((time.monotonic() - start) * 1000)
        return proc.returncode, proc.stdout, proc.stderr, duration
    except FileNotFoundError as exc:
        duration = int((time.monotonic() - start) * 1000)
        return 127, "", f"command not found: {exc}", duration
    except subprocess.TimeoutExpired as exc:
        duration = int((time.monotonic() - start) * 1000)
        return 124, exc.stdout or "", exc.stderr or f"timed out after {timeout}s", duration


# ---------------------------------------------------------------------------
# Built-in check factories. Each returns a zero-argument CheckFn so the agent
# can run them uniformly and so tests can inject alternatives.
# ---------------------------------------------------------------------------


def make_compile_check(repo_root: Path) -> CheckFn:
    def check() -> ValidationCheckResult:
        targets = [
            repo_root / "src" / "deepscientist" / "orchestration",
            repo_root / "src" / "deepscientist" / "runners" / "omp.py",
        ]
        files = []
        for target in targets:
            if target.is_dir():
                files.extend(sorted(target.glob("*.py")))
            elif target.exists():
                files.append(target)
        import py_compile

        start = time.monotonic()
        try:
            for path in files:
                py_compile.compile(str(path), doraise=True)
        except py_compile.PyCompileError as exc:
            return ValidationCheckResult(
                check=CHECK_COMPILE,
                ok=False,
                summary=f"compile failed: {exc.msg.splitlines()[0] if exc.msg else exc}",
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return ValidationCheckResult(
            check=CHECK_COMPILE,
            ok=True,
            summary=f"compiled {len(files)} modules",
            details={"file_count": len(files)},
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    return check


def make_no_template_engine_check(repo_root: Path) -> CheckFn:
    def check() -> ValidationCheckResult:
        import re

        forbidden = [
            repo_root / "src" / "deepscientist" / "orchestration",
            repo_root / "skills" / "deepscientist-linux-agent-setup",
        ]
        hits: list[str] = []
        pattern = re.compile(r"\{%.*?%\}|\{\{.*?\}\}")
        for base in forbidden:
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if not path.is_file() or path.suffix in {".pyc"}:
                    continue
                if path.suffix in {".j2", ".jinja", ".jinja2", ".tmpl"}:
                    hits.append(f"{path.relative_to(repo_root)}: template file")
                    continue
                if path.suffix in {".py", ".md", ".yaml", ".yml"}:
                    try:
                        text = path.read_text(encoding="utf-8")
                    except (OSError, UnicodeDecodeError):
                        continue
                    if pattern.search(text):
                        hits.append(f"{path.relative_to(repo_root)}: template syntax")
        return ValidationCheckResult(
            check=CHECK_NO_TEMPLATE_ENGINE,
            ok=not hits,
            summary="no text templating engine found" if not hits else f"{len(hits)} template hit(s)",
            details={"hits": hits[:20]},
        )

    return check


def make_playbook_render_check(service_factory: Callable[[], Any]) -> CheckFn:
    """Drive the orchestration service through a full flow and render the playbook."""

    def check() -> ValidationCheckResult:
        start = time.monotonic()
        from .models import (
            EVT_CONFIG_COMPLETED,
            EVT_ENVIRONMENT_DETECTED,
            EVT_INSTALL_COMPLETED,
            EVT_PREFLIGHT_RESULT,
            EVT_VALIDATION_RESULT,
        )

        service = service_factory()
        obj = service.create_object(
            name="merge-gate-check", environment="linux", kind=KIND_MERGE_GATE
        )
        for event, payload in [
            (EVT_ENVIRONMENT_DETECTED, {"environment": "linux"}),
            (EVT_PREFLIGHT_RESULT, {"ok": True}),
            (EVT_INSTALL_COMPLETED, {"ok": True}),
            (EVT_CONFIG_COMPLETED, {"ok": True}),
            (EVT_VALIDATION_RESULT, {"ok": True}),
        ]:
            service.ingest_event(obj.object_id, event, payload)
        rendered = service.render_playbook(obj.object_id)
        verdict = rendered.get("verdict") or {}
        if not rendered.get("ok") or not verdict.get("ready_to_advance"):
            return ValidationCheckResult(
                check=CHECK_PLAYBOOK_RENDER,
                ok=False,
                summary="playbook verdict not ready",
                details=verdict,
                duration_ms=int((time.monotonic() - start) * 1000),
            )
        return ValidationCheckResult(
            check=CHECK_PLAYBOOK_RENDER,
            ok=True,
            summary="playbook rendered and closed-loop verdict ready",
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    return check


def make_audit_check(repo_root: Path, auditor: str, check_name: str) -> CheckFn:
    def check() -> ValidationCheckResult:
        start = time.monotonic()
        import sys

        rc, out, err, _ = _run_subprocess(
            [sys.executable, str(repo_root / "scripts" / "audits" / auditor)],
            cwd=repo_root,
            timeout=60,
        )
        ok = rc == 0
        return ValidationCheckResult(
            check=check_name,
            ok=ok,
            summary="audit passed" if ok else "audit failed",
            details={"returncode": rc, "tail": (out or err)[-500:]},
            duration_ms=int((time.monotonic() - start) * 1000),
        )

    return check


def make_unit_tests_check(repo_root: Path, pytest_path: str | None = None) -> CheckFn:
    def check() -> ValidationCheckResult:
        import sys

        # Prefer `python -m pytest` so it resolves against the active interpreter;
        # fall back to a bare `pytest` binary only if explicitly requested.
        if pytest_path:
            argv = [pytest_path]
        else:
            argv = [sys.executable, "-m", "pytest"]
        rc, out, err, duration = _run_subprocess(
            [*argv, "tests/orchestration", "tests/test_windows_wsl_skill.py", "-q"],
            cwd=repo_root,
            timeout=300,
        )
        ok = rc == 0
        return ValidationCheckResult(
            check=CHECK_UNIT_TESTS,
            ok=ok,
            summary="unit tests passed" if ok else "unit tests failed",
            details={
                "returncode": rc,
                "tail": (out or err)[-1200:],
            },
            duration_ms=duration,
        )

    return check


# ---------------------------------------------------------------------------
# The internal agent
# ---------------------------------------------------------------------------


class InternalValidationAgent:
    """Runs a closed validation loop over an orchestration object.

    The agent is the *internal agent* inside DeepScientist: it reads the
    current DB state, executes each registered check as an atomic
    action, records typed results, and asks the deterministic engine to
    advance. It retries failed checks up to ``max_attempts`` times and
    emits ``merge.ready`` or ``merge.blocked`` when the loop terminates.
    """

    def __init__(
        self,
        service: Any,
        *,
        repo_root: Path,
        checks: Iterable[str] = DEFAULT_MERGE_CHECKS,
        max_attempts: int = 3,
        python_executable: str | None = None,
        extra_checks: dict[str, CheckFn] | None = None,
    ) -> None:
        self.service = service
        self.repo_root = Path(repo_root)
        self.check_names = tuple(checks)
        self.max_attempts = max(1, int(max_attempts))
        self.python = python_executable or "python3"
        self._extra_checks: dict[str, CheckFn] = dict(extra_checks or {})
        self._checks = self._build_default_checks()

    # ------------------------------------------------------------------ setup

    def _build_default_checks(self) -> dict[str, CheckFn]:
        def service_factory() -> Any:
            return self.service

        return {
            CHECK_COMPILE: make_compile_check(self.repo_root),
            CHECK_NO_TEMPLATE_ENGINE: make_no_template_engine_check(self.repo_root),
            CHECK_PLAYBOOK_RENDER: make_playbook_render_check(service_factory),
            CHECK_AUDIT_LINUX: make_audit_check(
                self.repo_root, "audit_linux_agent_skill.py", CHECK_AUDIT_LINUX
            ),
            CHECK_AUDIT_WINDOWS: make_audit_check(
                self.repo_root, "audit_windows_wsl_skill.py", CHECK_AUDIT_WINDOWS
            ),
            CHECK_UNIT_TESTS: make_unit_tests_check(self.repo_root, self.python if self.python != "python3" else None),
            **self._extra_checks,
        }

    def register_check(self, name: str, fn: CheckFn) -> None:
        self._checks[name] = fn

    # ------------------------------------------------------------------ loop

    def run(self, object_id: str) -> dict[str, Any]:
        """Execute the validation loop until all checks pass or attempts exhaust."""
        from .models import MergeReadinessReport

        all_results: list[ValidationCheckResult] = []
        attempts = 0

        for attempt in range(1, self.max_attempts + 1):
            attempts = attempt
            self._emit(object_id, EVT_VALIDATION_RETRY, {"attempt": attempt})
            round_results: list[ValidationCheckResult] = []

            for check_name in self.check_names:
                fn = self._checks.get(check_name)
                if fn is None:
                    round_results.append(
                        ValidationCheckResult(
                            check=check_name,
                            ok=False,
                            summary=f"unknown check `{check_name}`",
                        )
                    )
                    continue
                self._emit(
                    object_id,
                    EVT_VALIDATION_CHECK_REQUESTED,
                    {"check": check_name, "attempt": attempt},
                )
                try:
                    result = fn()
                except Exception as exc:  # noqa: BLE001
                    result = ValidationCheckResult(
                        check=check_name,
                        ok=False,
                        summary=f"check raised: {exc}",
                    )
                round_results.append(result)
                all_results.append(result)
                self._emit(
                    object_id,
                    EVT_VALIDATION_CHECK_RESULT,
                    result.to_dict(),
                )
                logger.info(
                    "merge-gate check %s ok=%s (%s)",
                    check_name,
                    result.ok,
                    result.summary,
                )

            failed = [r for r in round_results if not r.ok and r.required]
            if not failed:
                return self._finalize_success(object_id, attempts, all_results)

        return self._finalize_failure(object_id, attempts, all_results)

    # ------------------------------------------------------------------ finalize

    def _finalize_success(
        self, object_id: str, attempts: int, results: list[ValidationCheckResult]
    ) -> dict[str, Any]:
        passed = tuple(r.check for r in results if r.ok)
        report = {
            "object_id": object_id,
            "ready": True,
            "passed": sorted(set(passed)),
            "failed": [],
            "attempts": attempts,
            "check_count": len(results),
        }
        # Advance the deterministic engine to READY.
        self.service.ingest_event(
            object_id,
            EVT_VALIDATION_RESULT,
            {"ok": True, "attempts": attempts},
        )
        self._emit(object_id, EVT_MERGE_READY, report)
        # Closed-loop verdict from the typed playbook.
        verdict: dict[str, Any] = {}
        try:
            rendered = self.service.render_playbook(object_id)
            verdict = rendered.get("verdict") or {}
        except Exception:  # noqa: BLE001
            pass
        return {**report, "verdict": verdict}

    def _finalize_failure(
        self, object_id: str, attempts: int, results: list[ValidationCheckResult]
    ) -> dict[str, Any]:
        failed_names = sorted({r.check for r in results if not r.ok and r.required})
        blocker = "; ".join(
            f"{r.check}: {r.summary}" for r in results if not r.ok and r.required
        )
        report = {
            "object_id": object_id,
            "ready": False,
            "passed": sorted({r.check for r in results if r.ok}),
            "failed": failed_names,
            "attempts": attempts,
            "blocker": blocker,
            "check_count": len(results),
        }
        # Move the deterministic engine to FAILED.
        self.service.ingest_event(
            object_id,
            EVT_VALIDATION_RESULT,
            {"ok": False, "reason": blocker or "validation failed after retries"},
        )
        self._emit(object_id, EVT_MERGE_BLOCKED, report)
        return report

    # ------------------------------------------------------------------ helpers

    def _emit(self, object_id: str, event_type: str, payload: dict[str, Any]) -> None:
        try:
            self.service.store.append_event(object_id, event_type, payload)
        except Exception:  # noqa: BLE001
            logger.warning("failed to emit event %s", event_type, exc_info=True)

    def closed_loop_verdict(self, object_id: str) -> ClosedLoopVerdict:
        """Convenience: render the current typed closed-loop verdict."""
        from .schemas import EventDrivenPlaybook, Environment, EnvironmentProfile, ObjectState

        obj = self.service.store.get_object(object_id)
        if obj is None:
            raise KeyError(object_id)
        latest = self.service.store.latest_decision(object_id)
        from .schemas import Action, ActionKind, Decision

        actions = [
            Action(
                kind=ActionKind(str(a.get("kind") or "noop")),
                runner=a.get("runner"),
                reason=a.get("reason"),
            )
            for a in (latest or {}).get("actions", [])
            if isinstance(a, dict)
        ]
        decision = Decision(
            object_id=object_id,
            previous_state=ObjectState((latest or {}).get("previous_state", obj.state)),
            next_state=ObjectState((latest or {}).get("next_state", obj.state)),
            rationale=str((latest or {}).get("rationale") or ""),
            actions=actions,
        )
        from .decisions import EnvironmentRouter

        profile = EnvironmentRouter.profile_for(obj.environment)
        typed_profile = EnvironmentProfile(
            environment=Environment(profile.environment),
            runner=profile.runner,
            setup_skill=profile.setup_skill,
            package_manager=profile.package_manager,
            notes=profile.notes,
        )
        playbook = EventDrivenPlaybook(
            managed_object=self.service._to_typed_object(obj),
            decision=decision,
            profile=typed_profile,
        )
        return playbook.closed_loop_verdict()


def run_merge_gate(
    service: Any,
    *,
    repo_root: Path,
    object_name: str = "merge-gate",
    max_attempts: int = 2,
    checks: Iterable[str] = DEFAULT_MERGE_CHECKS,
) -> dict[str, Any]:
    """Convenience entry point: create a merge-gate object and run the loop."""
    obj = service.create_object(
        name=object_name,
        environment="linux",
        kind=KIND_MERGE_GATE,
    )
    # Drive it to the validating state deterministically.
    from .models import (
        EVT_CONFIG_COMPLETED,
        EVT_ENVIRONMENT_DETECTED,
        EVT_INSTALL_COMPLETED,
        EVT_PREFLIGHT_RESULT,
    )

    for event, payload in [
        (EVT_ENVIRONMENT_DETECTED, {"environment": "linux"}),
        (EVT_PREFLIGHT_RESULT, {"ok": True}),
        (EVT_INSTALL_COMPLETED, {"ok": True}),
        (EVT_CONFIG_COMPLETED, {"ok": True}),
    ]:
        service.ingest_event(obj.object_id, event, payload)

    agent = InternalValidationAgent(
        service,
        repo_root=repo_root,
        checks=checks,
        max_attempts=max_attempts,
    )
    return agent.run(obj.object_id)
