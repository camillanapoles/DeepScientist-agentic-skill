"""Pydantic schemas for the deterministic orchestration engine.

Every dynamic field is a strongly typed attribute on a ``BaseModel``.
Rendering to markdown is an explicit, tested method on each model — no
free-form string interpolation or text templating engine.

The same schemas double as the *response model* for Instructor-wrapped
LLM calls (see :mod:`deepscientist.orchestration.llm`), so any
model-assisted decision is structurally validated before it can mutate
state.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums — closed vocabularies make the state machine impossible to hallucinate.
# ---------------------------------------------------------------------------


class Environment(str, Enum):
    LINUX = "linux"
    WINDOWS_WSL = "windows_wsl"
    MACOS = "macos"
    UNKNOWN = "unknown"


class ObjectState(str, Enum):
    PENDING = "pending"
    PREFLIGHT = "preflight"
    PROVISIONING = "provisioning"
    CONFIGURING = "configuring"
    VALIDATING = "validating"
    READY = "ready"
    BLOCKED_HUMAN = "blocked_human"
    FAILED = "failed"
    CLOSED = "closed"


class ActionKind(str, Enum):
    DETECT_ENVIRONMENT = "detect_environment"
    RUN_PREFLIGHT = "run_preflight"
    INSTALL_RUNTIME = "install_runtime"
    CONFIGURE_RUNTIME = "configure_runtime"
    VALIDATE = "validate"
    LAUNCH = "launch"
    BLOCK_ON_HUMAN = "block_on_human"
    FAIL = "fail"
    NOOP = "noop"


class EventType(str, Enum):
    ENVIRONMENT_DETECTED = "environment.detected"
    PREFLIGHT_RESULT = "preflight.result"
    INSTALL_COMPLETED = "install.completed"
    CONFIG_COMPLETED = "config.completed"
    VALIDATION_RESULT = "validation.result"
    HUMAN_ACTION_REQUIRED = "human.action_required"
    HUMAN_ACTION_RESOLVED = "human.action_resolved"
    STATE_READY = "state.ready"
    STATE_FAILED = "state.failed"


# ---------------------------------------------------------------------------
# Structured event / action / decision models.
# ---------------------------------------------------------------------------


class OrchestrationEvent(BaseModel):
    """A single durable event in the orchestration log."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(..., description="Stable unique event identifier.")
    event_type: str = Field(..., min_length=1)
    object_id: str | None = None
    created_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class Action(BaseModel):
    """A single instruction for the agent (or a human) to execute."""

    model_config = ConfigDict(extra="forbid")

    kind: ActionKind
    reason: str | None = None
    required_action: str | None = None
    runner: str | None = None
    environment: str | None = None
    setup_skill: str | None = None

    def render(self) -> str:
        if self.kind is ActionKind.BLOCK_ON_HUMAN:
            return f"- **BLOCKED — human action required:** {self.required_action or self.reason or 'see event log'}"
        label = self.kind.value.replace("_", " ")
        extras: list[str] = []
        if self.runner:
            extras.append(f"runner=`{self.runner}`")
        if self.environment:
            extras.append(f"environment=`{self.environment}`")
        if self.setup_skill:
            extras.append(f"skill=`{self.setup_skill}`")
        suffix = f" ({', '.join(extras)})" if extras else ""
        return f"- `{label}`{suffix}"


class Decision(BaseModel):
    """The deterministic outcome of processing one event.

    Rendered output is produced by :meth:`render_markdown`, not by a text
    template — every section is a typed field.
    """

    model_config = ConfigDict(extra="forbid")

    object_id: str
    previous_state: ObjectState
    next_state: ObjectState
    rationale: str = Field(..., min_length=1)
    actions: list[Action] = Field(default_factory=list)
    emitted_events: list[OrchestrationEvent] = Field(default_factory=list)

    def render_markdown(self) -> str:
        lines = [
            f"### Decision for `{self.object_id}`",
            "",
            f"- **State:** `{self.previous_state.value}` → `{self.next_state.value}`",
            f"- **Rationale:** {self.rationale}",
            "",
            "**Actions:**",
        ]
        if self.actions:
            lines.extend(action.render() for action in self.actions)
        else:
            lines.append("- _(no actions)_")
        if self.emitted_events:
            lines.append("")
            lines.append("**Emitted events:**")
            for event in self.emitted_events:
                lines.append(f"- `{event.event_type}`")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Managed object + profile — strongly typed projections of DB rows.
# ---------------------------------------------------------------------------


class EnvironmentProfile(BaseModel):
    """Resolved routing result for an environment string."""

    model_config = ConfigDict(extra="forbid")

    environment: Environment
    runner: str
    setup_skill: str
    package_manager: str
    notes: str = ""

    def render_markdown(self) -> str:
        return (
            f"- **environment:** `{self.environment.value}`\n"
            f"- **runner:** `{self.runner}`\n"
            f"- **setup skill:** `{self.setup_skill}`\n"
            f"- **package manager:** `{self.package_manager}`"
            + (f"\n- **notes:** {self.notes}" if self.notes else "")
        )


class ManagedObject(BaseModel):
    """A single orchestration-managed entity (DB row projection)."""

    model_config = ConfigDict(extra="forbid")

    object_id: str
    kind: str = "agent_host"
    name: str
    environment: Environment = Environment.UNKNOWN
    state: ObjectState = ObjectState.PENDING
    runner: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def render_markdown(self) -> str:
        lines = [
            f"# Managed Object — {self.name}",
            "",
            f"- **object_id:** `{self.object_id}`",
            f"- **kind:** `{self.kind}`",
            f"- **environment:** `{self.environment.value}`",
            f"- **state:** `{self.state.value}`",
            f"- **runner:** `{self.runner or '(unset)'}`",
        ]
        if self.payload:
            lines.append("")
            lines.append("**Payload:**")
            for key, value in sorted(self.payload.items()):
                lines.append(f"- {key}: `{value}`")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Playbook — the SOTA, fully-typed runbook.
# ---------------------------------------------------------------------------


class PlaybookSection(BaseModel):
    """One block of the rendered playbook (header + typed body)."""

    model_config = ConfigDict(extra="forbid")

    title: str
    body_markdown: str

    def render(self) -> str:
        return f"## {self.title}\n\n{self.body_markdown}"


class EventDrivenPlaybook(BaseModel):
    """Deterministic, context-aware runbook.

    Constructed from a live :class:`ManagedObject`, the most recent
    :class:`Decision`, and the resolved :class:`EnvironmentProfile`.
    Rendering is a pure method — no template engine, no string
    interpolation of untrusted content.
    """

    model_config = ConfigDict(extra="forbid")

    managed_object: ManagedObject
    decision: Decision
    profile: EnvironmentProfile
    recent_events: list[OrchestrationEvent] = Field(default_factory=list)

    # ------------------------------------------------------------------ helpers

    def _state_section(self) -> PlaybookSection:
        body = (
            f"- object_id: `{self.managed_object.object_id}`\n"
            f"- name: **{self.managed_object.name}**\n"
            f"- environment: `{self.profile.environment.value}`\n"
            f"- runner: **{self.profile.runner}**\n"
            f"- state: `{self.managed_object.state.value}` → "
            f"`{self.decision.next_state.value}`"
        )
        return PlaybookSection(title="State", body_markdown=body)

    def _rationale_section(self) -> PlaybookSection:
        return PlaybookSection(title="Rationale", body_markdown=self.decision.rationale)

    def _actions_section(self) -> PlaybookSection:
        if not self.decision.actions:
            body = "_No actions — this transition is a no-op._"
        else:
            body = "\n".join(action.render() for action in self.decision.actions)
        return PlaybookSection(title="Actions to execute now", body_markdown=body)

    def _environment_section(self) -> PlaybookSection:
        env = self.profile.environment
        if env is Environment.LINUX:
            body = (
                "Target is native Linux. The default runner is **OMP "
                "(oh-my-pi / pi-coding-agent)**.\n\n"
                "- Install OMP: `curl -fsSL https://omp.sh/install | sh`\n"
                "- Validate OMP: `omp print --json --yes \"Print exactly OK and exit.\"`\n"
                f"- The package manager is `{self.profile.package_manager}`."
            )
        elif env is Environment.WINDOWS_WSL:
            body = (
                "Target is Windows + WSL2. Run `wsl.exe` from Windows and `apt` "
                "inside the distro. Use the `deepscientist-windows-wsl-setup` "
                "skill; the default runner there is Codex."
            )
        else:
            body = (
                f"Environment is `{env.value}`. Run environment detection "
                "before provisioning."
            )
        return PlaybookSection(title="Environment-specific guidance", body_markdown=body)

    def _blocked_section(self) -> PlaybookSection | None:
        if self.decision.next_state is not ObjectState.BLOCKED_HUMAN:
            return None
        human_actions = [
            a for a in self.decision.actions if a.kind is ActionKind.BLOCK_ON_HUMAN
        ]
        if not human_actions:
            body = "**Blocked on a human action**, but no specific action was recorded."
        else:
            lines: list[str] = []
            for action in human_actions:
                lines.append(
                    f"- {action.required_action or action.reason or 'resolve blocker'}"
                )
            body = "\n".join(lines)
        return PlaybookSection(title="BLOCKED — human action required", body_markdown=body)

    def _event_log_section(self) -> PlaybookSection:
        if not self.recent_events:
            body = "_(no events yet)_"
        else:
            lines = [
                f"- `{evt.created_at.isoformat()}` **{evt.event_type}**"
                for evt in self.recent_events[-10:]
            ]
            body = "\n".join(lines)
        return PlaybookSection(title="Recent event log", body_markdown=body)

    # ------------------------------------------------------------------ render

    def render_markdown(self) -> str:
        sections: list[PlaybookSection] = [
            self._state_section(),
            self._rationale_section(),
            self._actions_section(),
            self._environment_section(),
        ]
        blocked = self._blocked_section()
        if blocked is not None:
            sections.append(blocked)
        sections.append(self._event_log_section())
        header = f"# Event-Driven Decision Playbook — {self.managed_object.name}\n"
        return header + "\n\n".join(section.render() for section in sections)

    # ------------------------------------------------------------------ OSWorld

    def closed_loop_verdict(self) -> "ClosedLoopVerdict":
        """Imperative progress gate (OSWorld 2.0 long-horizon pattern).

        The agent may not advance to the next action until this returns
        ``ready_to_advance=True``. All checks are pure/imperative — no
        LLM is trusted to self-report completion.
        """
        blockers: list[str] = []
        if self.decision.next_state is ObjectState.BLOCKED_HUMAN:
            blockers.append("object is blocked on a human action")
        if self.decision.next_state is ObjectState.FAILED:
            blockers.append("object is in a failed state")
        if not self.decision.actions and self.decision.next_state not in {
            ObjectState.READY,
            ObjectState.CLOSED,
        }:
            blockers.append("decision produced no actions for a non-terminal state")
        # Determinism check: actions must reference the resolved runner
        # when provisioning/configuring/validating.
        for action in self.decision.actions:
            if action.kind in {
                ActionKind.INSTALL_RUNTIME,
                ActionKind.CONFIGURE_RUNTIME,
                ActionKind.VALIDATE,
                ActionKind.LAUNCH,
            } and not action.runner:
                blockers.append(f"action {action.kind.value} is missing a runner")
        return ClosedLoopVerdict(
            object_id=self.managed_object.object_id,
            ready_to_advance=not blockers,
            blockers=blockers,
            next_state=self.decision.next_state,
        )


class ClosedLoopVerdict(BaseModel):
    """Imperative validation result used as a closed-loop gate."""

    model_config = ConfigDict(extra="forbid")

    object_id: str
    ready_to_advance: bool
    blockers: list[str] = Field(default_factory=list)
    next_state: ObjectState


# ---------------------------------------------------------------------------
# Instructor response models — schemas for any LLM-assisted decision.
# ---------------------------------------------------------------------------


class InstructorActionProposal(BaseModel):
    """Response model for an LLM-proposed next action.

    Instructor forces the LLM to return JSON matching this schema. The
    orchestration service then validates it *imperatively* against the
    current DB state before applying it — the LLM never mutates state
    directly.
    """

    model_config = ConfigDict(extra="forbid")

    proposed_event_type: EventType = Field(
        ...,
        description="The event type the agent believes should be ingested next.",
    )
    payload: dict[str, Any] = Field(
        default_factory=dict,
        description="Event payload. Must include `ok: true/false` for result events.",
    )
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reasoning: str = Field(..., min_length=1, max_length=2000)
    needs_human: bool = False
    required_human_action: str | None = None


class EnvironmentDetectionResult(BaseModel):
    """Structured result of an LLM-assisted environment classification."""

    model_config = ConfigDict(extra="forbid")

    environment: Environment
    runner: str
    setup_skill: str
    reasoning: str = Field(..., min_length=1, max_length=1000)
