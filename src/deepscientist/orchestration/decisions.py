"""Deterministic decision engine for agentic orchestration.

The engine is intentionally pure: given a :class:`ManagedObject` and an
event, it returns a :class:`Decision` without touching the database or
the network. The service layer is responsible for persisting the
outcome and publishing emitted events.

Decisions are driven by an explicit transition table rather than an LLM
call, so orchestration is reproducible, testable, and auditable. The
agent (OMP/Codex) executes the *actions* the engine returns; it does
not decide the state machine itself.
"""
from __future__ import annotations

from typing import Any

from ..shared import generate_id, utc_now
from .models import (
    ACTION_BLOCK_ON_HUMAN,
    ACTION_CONFIGURE_RUNTIME,
    ACTION_DETECT_ENVIRONMENT,
    ACTION_FAIL,
    ACTION_INSTALL_RUNTIME,
    ACTION_LAUNCH,
    ACTION_NOOP,
    ACTION_RUN_PREFLIGHT,
    ACTION_VALIDATE,
    ENV_LINUX,
    ENV_MACOS,
    ENV_UNKNOWN,
    ENV_WINDOWS_WSL,
    EVT_CONFIG_COMPLETED,
    EVT_CONFIG_REQUESTED,
    EVT_ENVIRONMENT_DETECTED,
    EVT_FAILED,
    EVT_HUMAN_ACTION_REQUIRED,
    EVT_HUMAN_ACTION_RESOLVED,
    EVT_INSTALL_COMPLETED,
    EVT_INSTALL_REQUESTED,
    EVT_PREFLIGHT_RESULT,
    EVT_READY,
    EVT_VALIDATION_REQUESTED,
    EVT_VALIDATION_RESULT,
    STATE_BLOCKED_HUMAN,
    STATE_CLOSED,
    STATE_CONFIGURING,
    STATE_FAILED,
    STATE_PENDING,
    STATE_PREFLIGHT,
    STATE_PROVISIONING,
    STATE_READY,
    STATE_VALIDATING,
    SUPPORTED_ENVIRONMENTS,
    Decision,
    EnvironmentProfile,
    ManagedObject,
)

#: Default runner per environment. OMP (oh-my-pi / pi-coding-agent) is the
#: default for Linux per the product requirement; Codex remains the
#: default for Windows/WSL because the verified WSL path is Codex-pinned.
DEFAULT_RUNNER_BY_ENV: dict[str, str] = {
    ENV_LINUX: "omp",
    ENV_WINDOWS_WSL: "codex",
    ENV_MACOS: "omp",
    ENV_UNKNOWN: "omp",
}

#: Setup skill id per environment.
SETUP_SKILL_BY_ENV: dict[str, str] = {
    ENV_LINUX: "deepscientist-linux-agent-setup",
    ENV_WINDOWS_WSL: "deepscientist-windows-wsl-setup",
    ENV_MACOS: "deepscientist-linux-agent-setup",
    ENV_UNKNOWN: "deepscientist-linux-agent-setup",
}

#: Package manager hint per environment.
PACKAGE_MANAGER_BY_ENV: dict[str, str] = {
    ENV_LINUX: "apt",
    ENV_WINDOWS_WSL: "apt-in-wsl",
    ENV_MACOS: "brew",
    ENV_UNKNOWN: "unknown",
}

_RUNNER_BINARY_BY_ENV: dict[str, dict[str, str]] = {
    ENV_LINUX: {"omp": "omp", "codex": "codex", "claude": "claude", "kimi": "kimi"},
    ENV_WINDOWS_WSL: {"omp": "omp", "codex": "codex"},
    ENV_MACOS: {"omp": "omp", "codex": "codex"},
}


class EnvironmentRouter:
    """Resolves the correct runtime profile from the managed object's environment.

    The environment comes from the API/DB (set by ``environment.detected``
    events), never inferred from the CI host OS. This allows a Linux
    backend to orchestrate a Windows/WSL target and vice versa.
    """

    @staticmethod
    def normalize_environment(raw: str | None) -> str:
        value = str(raw or "").strip().lower().replace("-", "_").replace(" ", "_")
        if value in {"linux", "ubuntu", "debian", "wsl2_linux"}:
            return ENV_LINUX
        if value in {"windows", "win32", "windows_wsl", "wsl", "wsl2"}:
            return ENV_WINDOWS_WSL
        if value in {"macos", "darwin", "osx"}:
            return ENV_MACOS
        return ENV_UNKNOWN

    @classmethod
    def profile_for(
        cls,
        environment: str,
        *,
        runner_override: str | None = None,
        object_payload: dict[str, Any] | None = None,
    ) -> EnvironmentProfile:
        env = cls.normalize_environment(environment)
        payload = object_payload or {}
        runner = str(runner_override or payload.get("runner_override") or "").strip().lower()
        if not runner:
            runner = DEFAULT_RUNNER_BY_ENV[env]
        notes = ""
        if env == ENV_UNKNOWN:
            notes = "Environment is unknown; run environment detection before provisioning."
        elif env == ENV_WINDOWS_WSL:
            notes = "Target is Windows host + WSL2; run wsl.exe from Windows and apt inside the distro."
        elif env == ENV_LINUX:
            notes = "Target is a native Linux user environment; OMP/pi-coding-agent is the default runner."
        return EnvironmentProfile(
            environment=env,
            runner=runner,
            setup_skill=SETUP_SKILL_BY_ENV[env],
            package_manager=PACKAGE_MANAGER_BY_ENV[env],
            notes=notes,
        )

    @classmethod
    def is_supported(cls, environment: str) -> bool:
        return cls.normalize_environment(environment) in SUPPORTED_ENVIRONMENTS


# A transition is keyed by (state, event_type) and returns a dict of
# outcome fields. Keeping it table-driven makes the state machine easy to
# audit and extend.
TransitionFn = Any  # callable[[ManagedObject, dict], dict]


def _action(kind: str, **fields: Any) -> dict[str, Any]:
    return {"kind": kind, **fields}


def _ok(
    obj: ManagedObject,
    next_state: str,
    rationale: str,
    *,
    actions: list[dict[str, Any]] | None = None,
    emitted: list[dict[str, Any]] | None = None,
    runner: str | None = None,
    environment: str | None = None,
    payload_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "next_state": next_state,
        "rationale": rationale,
        "actions": tuple(actions or []),
        "emitted": tuple(emitted or []),
        "runner": runner if runner is not None else obj.runner,
        "environment": environment if environment is not None else obj.environment,
        "payload_patch": payload_patch or {},
    }


def _block_human(obj: ManagedObject, reason: str, *, required_action: str) -> dict[str, Any]:
    return _ok(
        obj,
        STATE_BLOCKED_HUMAN,
        reason,
        actions=[_action(ACTION_BLOCK_ON_HUMAN, reason=reason, required_action=required_action)],
        emitted=[
            {
                "event_type": EVT_HUMAN_ACTION_REQUIRED,
                "payload": {"reason": reason, "required_action": required_action},
            }
        ],
        payload_patch={"blocked_reason": reason, "required_action": required_action},
    )


def _fail(obj: ManagedObject, reason: str) -> dict[str, Any]:
    return _ok(
        obj,
        STATE_FAILED,
        reason,
        actions=[_action(ACTION_FAIL, reason=reason)],
        emitted=[{"event_type": EVT_FAILED, "payload": {"reason": reason}}],
        payload_patch={"failure_reason": reason},
    )


class DecisionEngine:
    """Pure state-machine evaluator."""

    def __init__(self) -> None:
        self._transitions: dict[tuple[str, str], TransitionFn] = {}
        self._register_defaults()

    # ------------------------------------------------------------------ registry

    def on(self, state: str, event_type: str) -> Any:
        def decorator(fn: TransitionFn) -> TransitionFn:
            self._transitions[(state, event_type)] = fn
            return fn

        return decorator

    def _register_defaults(self) -> None:
        # ---- PENDING ----------------------------------------------------------
        @self.on(STATE_PENDING, EVT_ENVIRONMENT_DETECTED)
        def _(obj: ManagedObject, payload: dict) -> dict:
            raw_env = payload.get("environment")
            env = EnvironmentRouter.normalize_environment(raw_env)
            if not EnvironmentRouter.is_supported(env):
                return _block_human(
                    obj,
                    f"Unsupported or unknown environment: {raw_env!r}",
                    required_action="Confirm the target OS/environment and re-send environment.detected.",
                )
            profile = EnvironmentRouter.profile_for(env, object_payload=obj.payload)
            return _ok(
                obj,
                STATE_PREFLIGHT,
                f"Environment resolved to {env}; moving to preflight using runner {profile.runner}.",
                actions=[
                    _action(
                        ACTION_RUN_PREFLIGHT,
                        environment=env,
                        runner=profile.runner,
                        setup_skill=profile.setup_skill,
                    )
                ],
                runner=profile.runner,
                environment=env,
                payload_patch={
                    "environment_raw": raw_env,
                    "package_manager": profile.package_manager,
                },
            )

        # ---- PREFLIGHT --------------------------------------------------------
        @self.on(STATE_PREFLIGHT, EVT_PREFLIGHT_RESULT)
        def _(obj: ManagedObject, payload: dict) -> dict:
            if not payload.get("ok"):
                reason = str(payload.get("reason") or "Preflight failed")
                if payload.get("requires_human"):
                    return _block_human(obj, reason, required_action=str(payload.get("required_action") or "Resolve preflight blocker."))
                return _fail(obj, reason)
            return _ok(
                obj,
                STATE_PROVISIONING,
                "Preflight passed; requesting runtime installation.",
                actions=[_action(ACTION_INSTALL_RUNTIME, runner=obj.runner, environment=obj.environment)],
                emitted=[{"event_type": EVT_INSTALL_REQUESTED, "payload": {"runner": obj.runner}}],
                payload_patch={"preflight": dict(payload)},
            )

        # ---- PROVISIONING -----------------------------------------------------
        @self.on(STATE_PROVISIONING, EVT_INSTALL_COMPLETED)
        def _(obj: ManagedObject, payload: dict) -> dict:
            if not payload.get("ok"):
                reason = str(payload.get("reason") or "Runtime installation failed")
                if payload.get("requires_human"):
                    return _block_human(obj, reason, required_action=str(payload.get("required_action") or "Install the runtime manually."))
                return _fail(obj, reason)
            return _ok(
                obj,
                STATE_CONFIGURING,
                "Runtime installed; requesting configuration.",
                actions=[_action(ACTION_CONFIGURE_RUNTIME, runner=obj.runner)],
                emitted=[{"event_type": EVT_CONFIG_REQUESTED, "payload": {}}],
                payload_patch={"install": dict(payload)},
            )

        # ---- CONFIGURING ------------------------------------------------------
        @self.on(STATE_CONFIGURING, EVT_CONFIG_COMPLETED)
        def _(obj: ManagedObject, payload: dict) -> dict:
            if not payload.get("ok"):
                reason = str(payload.get("reason") or "Configuration failed")
                if payload.get("requires_human"):
                    return _block_human(obj, reason, required_action=str(payload.get("required_action") or "Complete configuration."))
                return _fail(obj, reason)
            return _ok(
                obj,
                STATE_VALIDATING,
                "Configuration applied; requesting validation.",
                actions=[_action(ACTION_VALIDATE, runner=obj.runner)],
                emitted=[{"event_type": EVT_VALIDATION_REQUESTED, "payload": {}}],
                payload_patch={"config": dict(payload)},
            )

        # ---- VALIDATING -------------------------------------------------------
        @self.on(STATE_VALIDATING, EVT_VALIDATION_RESULT)
        def _(obj: ManagedObject, payload: dict) -> dict:
            if not payload.get("ok"):
                reason = str(payload.get("reason") or "Validation failed")
                if payload.get("requires_human"):
                    return _block_human(obj, reason, required_action=str(payload.get("required_action") or "Resolve validation blocker."))
                return _fail(obj, reason)
            return _ok(
                obj,
                STATE_READY,
                "Validation passed; the environment is ready.",
                actions=[_action(ACTION_LAUNCH, runner=obj.runner)],
                emitted=[{"event_type": EVT_READY, "payload": {"runner": obj.runner}}],
                payload_patch={"validation": dict(payload)},
            )

        # ---- BLOCKED_HUMAN <-> resume ----------------------------------------
        @self.on(STATE_BLOCKED_HUMAN, EVT_HUMAN_ACTION_RESOLVED)
        def _(obj: ManagedObject, payload: dict) -> dict:
            # Resume from whichever stage was blocked, recorded in payload.
            resume_at = str(payload.get("resume_state") or STATE_PREFLIGHT)
            action_kind = {
                STATE_PREFLIGHT: ACTION_RUN_PREFLIGHT,
                STATE_PROVISIONING: ACTION_INSTALL_RUNTIME,
                STATE_CONFIGURING: ACTION_CONFIGURE_RUNTIME,
                STATE_VALIDATING: ACTION_VALIDATE,
            }.get(resume_at, ACTION_DETECT_ENVIRONMENT)
            return _ok(
                obj,
                resume_at,
                f"Human blocker resolved; resuming at {resume_at}.",
                actions=[_action(action_kind, runner=obj.runner, environment=obj.environment)],
                payload_patch={"blocked_reason": None, "required_action": None},
            )

        # ---- Terminal states are no-ops ---------------------------------------
        def _terminal_noop(obj: ManagedObject, payload: dict) -> dict:
            return _ok(
                obj,
                obj.state,
                f"Object is in terminal state {obj.state}; event ignored.",
                actions=[_action(ACTION_NOOP, terminal=obj.state)],
            )

        for terminal in (STATE_READY, STATE_FAILED, STATE_CLOSED):
            for evt in (EVT_ENVIRONMENT_DETECTED, EVT_PREFLIGHT_RESULT):
                self._transitions[(terminal, evt)] = _terminal_noop

    # ------------------------------------------------------------------ evaluate

    def evaluate(self, obj: ManagedObject, event_type: str, payload: dict[str, Any] | None = None) -> Decision:
        data = payload or {}
        handler = self._transitions.get((obj.state, event_type))
        previous_state = obj.state
        if handler is None:
            # Unknown transition: deterministic no-op, never crash.
            return Decision(
                object_id=obj.object_id,
                previous_state=previous_state,
                next_state=previous_state,
                actions=(_action(ACTION_NOOP, reason=f"No transition for ({previous_state}, {event_type})"),),
                emitted_events=(),
                rationale=f"No transition registered for state={previous_state!r} event={event_type!r}.",
            )
        outcome = handler(obj, data)
        emitted_events = tuple(
            {
                "event_id": generate_id("evt"),
                "event_type": evt["event_type"],
                "object_id": obj.object_id,
                "created_at": utc_now(),
                "payload": evt.get("payload", {}),
            }
            for evt in outcome.get("emitted", ())
        )
        return Decision(
            object_id=obj.object_id,
            previous_state=previous_state,
            next_state=outcome["next_state"],
            actions=outcome.get("actions", ()),
            emitted_events=emitted_events,
            rationale=outcome["rationale"],
        )

    def apply_outcome(self, obj: ManagedObject, decision: Decision, original_payload: dict[str, Any] | None = None) -> ManagedObject:
        """Project a decision back onto an in-memory object (used by tests)."""
        data = original_payload or {}
        patch: dict[str, Any] = {}
        if decision.next_state == STATE_PREFLIGHT:
            env = data.get("environment")
            profile = EnvironmentRouter.profile_for(env or obj.environment, object_payload=obj.payload)
            patch = {
                "environment": profile.environment,
                "runner": profile.runner,
            }
        # Carry known payload patches through the transition by re-deriving
        # from the event payload for the relevant states.
        new_payload = dict(obj.payload)
        if decision.next_state in {STATE_PROVISIONING, STATE_CONFIGURING, STATE_VALIDATING, STATE_READY, STATE_FAILED, STATE_BLOCKED_HUMAN}:
            for key in ("preflight", "install", "config", "validation", "failure_reason", "blocked_reason", "required_action"):
                if key in data and key not in patch:
                    new_payload[key] = data[key]
        return ManagedObject(
            object_id=obj.object_id,
            kind=obj.kind,
            name=obj.name,
            environment=patch.get("environment", obj.environment),
            state=decision.next_state,
            runner=patch.get("runner", obj.runner),
            payload=new_payload,
            created_at=obj.created_at,
            updated_at=utc_now(),
        )
