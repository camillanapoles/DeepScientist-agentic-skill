"""Tests for the internal self-validating agent (merge-readiness loop).

The agent runs checks imperatively, feeds results through the deterministic
state machine, and retries until success or the attempt budget is exhausted.
Tests inject fast check functions so the loop is fully deterministic.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deepscientist.orchestration import (  # noqa: E402
    InternalValidationAgent,
    OrchestrationService,
    create_store,
)
from deepscientist.orchestration.agent import (  # noqa: E402
    CHECK_AUDIT_LINUX,
    CHECK_COMPILE,
    CHECK_NO_TEMPLATE_ENGINE,
    ValidationCheckResult,
    run_merge_gate,
)
from deepscientist.orchestration.models import (  # noqa: E402
    EVT_MERGE_BLOCKED,
    EVT_MERGE_READY,
    EVT_VALIDATION_CHECK_RESULT,
    STATE_FAILED,
    STATE_READY,
)


def _service(tmp_path: Path) -> OrchestrationService:
    return OrchestrationService(create_store(tmp_path / "gate.db"))


def _ok(check: str, summary: str = "ok") -> ValidationCheckResult:
    return ValidationCheckResult(check=check, ok=True, summary=summary)


def _fail(check: str, summary: str = "nope") -> ValidationCheckResult:
    return ValidationCheckResult(check=check, ok=False, summary=summary)


def test_all_checks_pass_reaches_ready_and_emits_merge_ready(tmp_path) -> None:
    service = _service(tmp_path)
    obj = service.create_object(name="gate", environment="linux", kind="merge_gate")
    # Drive to validating.
    from deepscientist.orchestration.models import (
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
        repo_root=tmp_path,
        checks=[CHECK_COMPILE, CHECK_NO_TEMPLATE_ENGINE],
        max_attempts=1,
    )
    # Inject always-passing checks to avoid subprocess/pytest dependency.
    agent.register_check(CHECK_COMPILE, lambda: _ok(CHECK_COMPILE))
    agent.register_check(CHECK_NO_TEMPLATE_ENGINE, lambda: _ok(CHECK_NO_TEMPLATE_ENGINE))

    report = agent.run(obj.object_id)

    assert report["ready"] is True
    assert report["attempts"] == 1
    assert service.get_object(obj.object_id).state == STATE_READY
    # merge.ready event was emitted.
    events = service.get_events(obj.object_id)
    assert any(e["event_type"] == EVT_MERGE_READY for e in events)


def test_failed_checks_are_retried_then_block(tmp_path) -> None:
    service = _service(tmp_path)
    obj = service.create_object(name="gate", environment="linux", kind="merge_gate")

    call_count = {"n": 0}

    def flaky() -> ValidationCheckResult:
        call_count["n"] += 1
        # Fails on attempt 1, passes on attempt 2.
        if call_count["n"] < 2:
            return _fail(CHECK_COMPILE, "transient")
        return _ok(CHECK_COMPILE, "recovered")

    agent = InternalValidationAgent(
        service,
        repo_root=tmp_path,
        checks=[CHECK_COMPILE],
        max_attempts=2,
    )
    agent.register_check(CHECK_COMPILE, flaky)

    # Object must be in validating state for validation.result to advance.
    from deepscientist.orchestration.models import (
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

    report = agent.run(obj.object_id)
    assert report["ready"] is True
    assert call_count["n"] == 2
    assert service.get_object(obj.object_id).state == STATE_READY


def test_permanent_failure_emits_merge_blocked(tmp_path) -> None:
    service = _service(tmp_path)
    obj = service.create_object(name="gate", environment="linux", kind="merge_gate")
    from deepscientist.orchestration.models import (
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
        repo_root=tmp_path,
        checks=[CHECK_COMPILE],
        max_attempts=2,
    )
    agent.register_check(CHECK_COMPILE, lambda: _fail(CHECK_COMPILE, "permanent"))

    report = agent.run(obj.object_id)
    assert report["ready"] is False
    assert CHECK_COMPILE in report["failed"]
    assert service.get_object(obj.object_id).state == STATE_FAILED
    events = service.get_events(obj.object_id)
    assert any(e["event_type"] == EVT_MERGE_BLOCKED for e in events)


def test_each_check_persists_typed_result_events(tmp_path) -> None:
    service = _service(tmp_path)
    obj = service.create_object(name="gate", environment="linux", kind="merge_gate")
    agent = InternalValidationAgent(
        service,
        repo_root=tmp_path,
        checks=[CHECK_COMPILE, CHECK_NO_TEMPLATE_ENGINE],
        max_attempts=1,
    )
    agent.register_check(CHECK_COMPILE, lambda: _ok(CHECK_COMPILE))
    agent.register_check(CHECK_NO_TEMPLATE_ENGINE, lambda: _ok(CHECK_NO_TEMPLATE_ENGINE))

    agent.run(obj.object_id)
    events = service.get_events(obj.object_id)
    results = [e for e in events if e["event_type"] == EVT_VALIDATION_CHECK_RESULT]
    assert len(results) == 2
    assert {e["payload"]["check"] for e in results} == {CHECK_COMPILE, CHECK_NO_TEMPLATE_ENGINE}
    assert all(e["payload"]["ok"] is True for e in results)


def test_run_merge_gate_convenience_runs_real_compile_check(tmp_path) -> None:
    """The real compile check must pass against the actual source tree."""
    service = _service(tmp_path)
    # Only run the lightweight compile + no-template checks for speed.
    report = run_merge_gate(
        service,
        repo_root=SRC.parents[1],  # repo root
        object_name="real-gate",
        max_attempts=1,
        checks=[CHECK_COMPILE, CHECK_NO_TEMPLATE_ENGINE],
    )
    assert report["ready"] is True
    assert CHECK_COMPILE in report["passed"]
    assert CHECK_NO_TEMPLATE_ENGINE in report["passed"]


def test_audit_linux_check_is_registered(tmp_path) -> None:
    service = _service(tmp_path)
    agent = InternalValidationAgent(service, repo_root=tmp_path)
    assert CHECK_AUDIT_LINUX in agent._checks
    assert callable(agent._checks[CHECK_AUDIT_LINUX])
