"""Domain models for the deterministic orchestration engine.

All managed objects are persisted in SQLite and are the single source of
truth. The dataclasses here are the typed projection of those rows; the
service layer is responsible for (de)serialisation.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any

#: Environment kinds the orchestrator can route to.
ENV_LINUX = "linux"
ENV_WINDOWS_WSL = "windows_wsl"
ENV_MACOS = "macos"
ENV_UNKNOWN = "unknown"

SUPPORTED_ENVIRONMENTS = frozenset({ENV_LINUX, ENV_WINDOWS_WSL, ENV_MACOS})

#: Lifecycle states for a managed object (e.g. an agent host or setup session).
STATE_PENDING = "pending"
STATE_PREFLIGHT = "preflight"
STATE_PROVISIONING = "provisioning"
STATE_CONFIGURING = "configuring"
STATE_VALIDATING = "validating"
STATE_READY = "ready"
STATE_BLOCKED_HUMAN = "blocked_human"
STATE_FAILED = "failed"
STATE_CLOSED = "closed"

TERMINAL_STATES = frozenset({STATE_READY, STATE_FAILED, STATE_CLOSED})
ACTIVE_STATES = frozenset(
    {
        STATE_PENDING,
        STATE_PREFLIGHT,
        STATE_PROVISIONING,
        STATE_CONFIGURING,
        STATE_VALIDATING,
        STATE_BLOCKED_HUMAN,
    }
)

#: Object kinds.
KIND_AGENT_HOST = "agent_host"
KIND_RUNTIME = "runtime"
KIND_PROVIDER_AUTH = "provider_auth"
KIND_SETUP_SESSION = "setup_session"
KIND_VALIDATION = "validation"

#: Event types emitted/consumed by the engine.
EVT_ENVIRONMENT_DETECTED = "environment.detected"
EVT_PREFLIGHT_RESULT = "preflight.result"
EVT_INSTALL_REQUESTED = "install.requested"
EVT_INSTALL_COMPLETED = "install.completed"
EVT_CONFIG_REQUESTED = "config.requested"
EVT_CONFIG_COMPLETED = "config.completed"
EVT_VALIDATION_REQUESTED = "validation.requested"
EVT_VALIDATION_RESULT = "validation.result"
EVT_HUMAN_ACTION_REQUIRED = "human.action_required"
EVT_HUMAN_ACTION_RESOLVED = "human.action_resolved"
EVT_READY = "state.ready"
EVT_FAILED = "state.failed"

#: Canonical action identifiers returned inside decisions.
ACTION_DETECT_ENVIRONMENT = "detect_environment"
ACTION_RUN_PREFLIGHT = "run_preflight"
ACTION_INSTALL_RUNTIME = "install_runtime"
ACTION_CONFIGURE_RUNTIME = "configure_runtime"
ACTION_VALIDATE = "validate"
ACTION_LAUNCH = "launch"
ACTION_BLOCK_ON_HUMAN = "block_on_human"
ACTION_FAIL = "fail"
ACTION_NOOP = "noop"


@dataclass
class ManagedObject:
    """A single orchestration-managed entity.

    ``payload`` holds free-form, environment-specific state. It is the
    only place where environment-specific data lives; the deterministic
    state machine operates on ``state``, ``environment`` and a small set
    of well-known payload keys.
    """

    object_id: str
    kind: str
    name: str
    environment: str
    state: str = STATE_PENDING
    runner: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def well_known(self, key: str, default: Any = None) -> Any:
        value = self.payload.get(key)
        return value if value is not None else default


@dataclass(frozen=True)
class Decision:
    """The deterministic outcome of processing one event.

    A decision is pure: given the same object state + event, the engine
    always returns the same ``next_state``, ``actions`` and emitted
    events. This is what makes orchestration testable and auditable.
    """

    object_id: str
    previous_state: str
    next_state: str
    actions: tuple[dict[str, Any], ...]
    emitted_events: tuple[dict[str, Any], ...]
    rationale: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "previous_state": self.previous_state,
            "next_state": self.next_state,
            "actions": list(self.actions),
            "emitted_events": list(self.emitted_events),
            "rationale": self.rationale,
        }


@dataclass(frozen=True)
class EnvironmentProfile:
    """Resolved environment routing result."""

    environment: str
    runner: str
    setup_skill: str
    package_manager: str
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Internal self-validating agent (merge-readiness loop)
# ---------------------------------------------------------------------------

#: Object kinds specific to the internal validation agent.
KIND_MERGE_GATE = "merge_gate"

#: Event types emitted by the internal validation agent.
EVT_VALIDATION_CHECK_REQUESTED = "validation.check_requested"
EVT_VALIDATION_CHECK_RESULT = "validation.check_result"
EVT_VALIDATION_RETRY = "validation.retry"
EVT_VALIDATION_ALL_PASSED = "validation.all_passed"
EVT_MERGE_READY = "merge.ready"
EVT_MERGE_BLOCKED = "merge.blocked"

#: Canonical validation-check identifiers.
CHECK_COMPILE = "compile"
CHECK_AUDIT_LINUX = "audit_linux"
CHECK_AUDIT_WINDOWS = "audit_windows"
CHECK_UNIT_TESTS = "unit_tests"
CHECK_NO_TEMPLATE_ENGINE = "no_template_engine"
CHECK_PLAYBOOK_RENDER = "playbook_render"

#: The ordered, default set of checks the internal agent runs to qualify a
#: change for merge. Each check is a pure callable that returns a
#: :class:`ValidationCheckResult`.
DEFAULT_MERGE_CHECKS: tuple[str, ...] = (
    CHECK_COMPILE,
    CHECK_NO_TEMPLATE_ENGINE,
    CHECK_PLAYBOOK_RENDER,
    CHECK_AUDIT_LINUX,
    CHECK_AUDIT_WINDOWS,
    CHECK_UNIT_TESTS,
)


@dataclass(frozen=True)
class ValidationCheckResult:
    """Outcome of a single validation check inside the closed loop."""

    check: str
    ok: bool
    summary: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    required: bool = True
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MergeReadinessReport:
    """Aggregate result of one full pass of the validation loop."""

    object_id: str
    ready: bool
    passed: tuple[str, ...]
    failed: tuple[str, ...]
    attempts: int
    results: tuple[ValidationCheckResult, ...]
    blocker: str = ""

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["results"] = [r.to_dict() for r in self.results]
        return data
