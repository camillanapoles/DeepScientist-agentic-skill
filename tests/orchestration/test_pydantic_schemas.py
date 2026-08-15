"""Tests for the Pydantic-based orchestration schemas and Instructor client.

Every dynamic field is a typed attribute, and LLM-assisted decisions go
through Instructor with a Pydantic response model.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deepscientist.orchestration.schemas import (  # noqa: E402
    Action,
    ActionKind,
    ClosedLoopVerdict,
    Decision,
    Environment,
    EnvironmentDetectionResult,
    EnvironmentProfile,
    EventDrivenPlaybook,
    InstructorActionProposal,
    ManagedObject,
    ObjectState,
    OrchestrationEvent,
)


# ---------------------------------------------------------------------------
# Strong typing / validation
# ---------------------------------------------------------------------------

def test_managed_object_rejects_invalid_environment() -> None:
    with pytest.raises(Exception):
        ManagedObject(
            object_id="o1",
            name="x",
            environment="plan9",  # type: ignore[arg-type]
        )


def test_managed_object_rejects_invalid_state() -> None:
    with pytest.raises(Exception):
        ManagedObject(
            object_id="o1",
            name="x",
            state="hypersleep",  # type: ignore[arg-type]
        )


def test_action_renders_typed_fields() -> None:
    action = Action(
        kind=ActionKind.INSTALL_RUNTIME,
        runner="omp",
        environment="linux",
    )
    rendered = action.render()
    assert "install runtime" in rendered
    assert "omp" in rendered
    assert "linux" in rendered


def test_blocked_action_renders_human_required_action() -> None:
    action = Action(
        kind=ActionKind.BLOCK_ON_HUMAN,
        reason="no sudo",
        required_action="grant sudo access",
    )
    rendered = action.render()
    assert "BLOCKED" in rendered
    assert "grant sudo access" in rendered


def test_decision_renders_to_markdown_without_templates() -> None:
    decision = Decision(
        object_id="o1",
        previous_state=ObjectState.CONFIGURING,
        next_state=ObjectState.VALIDATING,
        rationale="config ok",
        actions=[Action(kind=ActionKind.VALIDATE, runner="omp")],
    )
    md = decision.render_markdown()
    assert "configuring" in md
    assert "validating" in md
    assert "config ok" in md
    assert "omp" in md
    assert "{%" not in md and "{{" not in md


# ---------------------------------------------------------------------------
# Typed playbook
# ---------------------------------------------------------------------------

def _playbook(state: ObjectState = ObjectState.VALIDATING) -> EventDrivenPlaybook:
    obj = ManagedObject(
        object_id="o1",
        name="linux-host",
        environment=Environment.LINUX,
        state=state,
        runner="omp",
    )
    profile = EnvironmentProfile(
        environment=Environment.LINUX,
        runner="omp",
        setup_skill="deepscientist-linux-agent-setup",
        package_manager="apt",
    )
    event = OrchestrationEvent(
        event_id="e1",
        event_type="validation.result",
        created_at=datetime(2026, 1, 1),
        payload={"ok": True},
    )
    decision = Decision(
        object_id="o1",
        previous_state=ObjectState.CONFIGURING,
        next_state=state,
        rationale="ready to validate",
        actions=[Action(kind=ActionKind.VALIDATE, runner="omp", environment="linux")],
        emitted_events=[event],
    )
    return EventDrivenPlaybook(
        managed_object=obj,
        decision=decision,
        profile=profile,
        recent_events=[event],
    )


def test_playbook_renders_all_sections() -> None:
    pb = _playbook()
    md = pb.render_markdown()
    for required in (
        "Event-Driven Decision Playbook",
        "linux-host",
        "omp",
        "State",
        "Rationale",
        "Actions to execute now",
        "Environment-specific guidance",
        "Recent event log",
        "curl -fsSL https://omp.sh/install | sh",
    ):
        assert required in md, f"missing {required!r}"
    assert "{%" not in md and "{{" not in md


def test_playbook_windows_section() -> None:
    obj = ManagedObject(
        object_id="o2", name="win", environment=Environment.WINDOWS_WSL, state=ObjectState.PREFLIGHT
    )
    profile = EnvironmentProfile(
        environment=Environment.WINDOWS_WSL,
        runner="codex",
        setup_skill="deepscientist-windows-wsl-setup",
        package_manager="apt-in-wsl",
    )
    decision = Decision(
        object_id="o2",
        previous_state=ObjectState.PENDING,
        next_state=ObjectState.PREFLIGHT,
        rationale="detected windows",
        actions=[Action(kind=ActionKind.RUN_PREFLIGHT, runner="codex")],
    )
    pb = EventDrivenPlaybook(managed_object=obj, decision=decision, profile=profile)
    md = pb.render_markdown()
    assert "wsl.exe" in md
    assert "codex" in md


def test_playbook_blocked_section() -> None:
    obj = ManagedObject(object_id="o3", name="blocked", state=ObjectState.BLOCKED_HUMAN)
    decision = Decision(
        object_id="o3",
        previous_state=ObjectState.PREFLIGHT,
        next_state=ObjectState.BLOCKED_HUMAN,
        rationale="needs reboot",
        actions=[
            Action(
                kind=ActionKind.BLOCK_ON_HUMAN,
                reason="pending reboot",
                required_action="reboot the machine",
            )
        ],
    )
    pb = EventDrivenPlaybook(managed_object=obj, decision=decision, profile=EnvironmentProfile(
        environment=Environment.LINUX, runner="omp", setup_skill="s", package_manager="apt"
    ))
    md = pb.render_markdown()
    assert "BLOCKED" in md
    assert "reboot the machine" in md


# ---------------------------------------------------------------------------
# Closed-loop imperative gate (OSWorld 2.0)
# ---------------------------------------------------------------------------

def test_closed_loop_verdict_passes_for_valid_flow() -> None:
    verdict = _playbook(ObjectState.VALIDATING).closed_loop_verdict()
    assert isinstance(verdict, ClosedLoopVerdict)
    assert verdict.ready_to_advance is True
    assert verdict.blockers == []


def test_closed_loop_blocks_when_action_missing_runner() -> None:
    obj = ManagedObject(object_id="o4", name="x", environment=Environment.LINUX, state=ObjectState.PROVISIONING)
    decision = Decision(
        object_id="o4",
        previous_state=ObjectState.PREFLIGHT,
        next_state=ObjectState.PROVISIONING,
        rationale="installing",
        actions=[Action(kind=ActionKind.INSTALL_RUNTIME)],  # no runner!
    )
    pb = EventDrivenPlaybook(
        managed_object=obj,
        decision=decision,
        profile=EnvironmentProfile(environment=Environment.LINUX, runner="omp", setup_skill="s", package_manager="apt"),
    )
    verdict = pb.closed_loop_verdict()
    assert verdict.ready_to_advance is False
    assert any("missing a runner" in b for b in verdict.blockers)


def test_closed_loop_blocks_on_blocked_human() -> None:
    obj = ManagedObject(object_id="o5", name="x", state=ObjectState.BLOCKED_HUMAN)
    decision = Decision(
        object_id="o5",
        previous_state=ObjectState.PREFLIGHT,
        next_state=ObjectState.BLOCKED_HUMAN,
        rationale="needs key",
        actions=[Action(kind=ActionKind.BLOCK_ON_HUMAN, required_action="provide API key")],
    )
    pb = EventDrivenPlaybook(
        managed_object=obj, decision=decision,
        profile=EnvironmentProfile(environment=Environment.LINUX, runner="omp", setup_skill="s", package_manager="apt"),
    )
    verdict = pb.closed_loop_verdict()
    assert verdict.ready_to_advance is False
    assert any("blocked" in b for b in verdict.blockers)


def test_closed_loop_blocks_when_no_actions_for_non_terminal_state() -> None:
    obj = ManagedObject(object_id="o6", name="x", state=ObjectState.PENDING)
    decision = Decision(
        object_id="o6", previous_state=ObjectState.PENDING, next_state=ObjectState.PENDING,
        rationale="noop", actions=[],
    )
    pb = EventDrivenPlaybook(
        managed_object=obj, decision=decision,
        profile=EnvironmentProfile(environment=Environment.LINUX, runner="omp", setup_skill="s", package_manager="apt"),
    )
    verdict = pb.closed_loop_verdict()
    assert verdict.ready_to_advance is False
    assert any("no actions" in b for b in verdict.blockers)


# ---------------------------------------------------------------------------
# Instructor response models
# ---------------------------------------------------------------------------

def test_instructor_proposal_model_accepts_valid_payload() -> None:
    proposal = InstructorActionProposal(
        proposed_event_type="preflight.result",
        payload={"ok": True},
        confidence=0.95,
        reasoning="preflight commands succeeded",
    )
    assert proposal.proposed_event_type.value == "preflight.result"


def test_instructor_proposal_rejects_bad_confidence() -> None:
    with pytest.raises(Exception):
        InstructorActionProposal(
            proposed_event_type="preflight.result",
            payload={},
            confidence=1.5,
            reasoning="x",
        )


def test_environment_detection_result_is_typed() -> None:
    result = EnvironmentDetectionResult(
        environment=Environment.LINUX,
        runner="omp",
        setup_skill="deepscientist-linux-agent-setup",
        reasoning="uname shows Linux",
    )
    assert result.environment is Environment.LINUX


# ---------------------------------------------------------------------------
# Service integration: typed playbook endpoint + LLM graceful degradation
# ---------------------------------------------------------------------------

def test_service_render_playbook_full_flow(tmp_path) -> None:
    from deepscientist.orchestration import OrchestrationService, create_store
    from deepscientist.orchestration.models import (
        EVT_CONFIG_COMPLETED, EVT_ENVIRONMENT_DETECTED, EVT_INSTALL_COMPLETED,
        EVT_PREFLIGHT_RESULT, EVT_VALIDATION_RESULT,
    )

    service = OrchestrationService(create_store(tmp_path / "s.db"))
    obj = service.create_object(name="svc-host", environment="linux")
    for evt, payload in [
        (EVT_ENVIRONMENT_DETECTED, {"environment": "linux"}),
        (EVT_PREFLIGHT_RESULT, {"ok": True}),
        (EVT_INSTALL_COMPLETED, {"ok": True}),
        (EVT_CONFIG_COMPLETED, {"ok": True}),
        (EVT_VALIDATION_RESULT, {"ok": True}),
    ]:
        service.ingest_event(obj.object_id, evt, payload)

    rendered = service.render_playbook(obj.object_id)
    assert rendered["ok"] is True
    assert "omp" in rendered["markdown"]
    assert rendered["verdict"]["next_state"] == "ready"
    assert rendered["verdict"]["ready_to_advance"] is True


def test_service_propose_action_degrades_gracefully_without_api_key(tmp_path) -> None:
    """Without OPENAI_API_KEY, Instructor proposals return unavailable, never crash."""
    import os
    from deepscientist.orchestration import OrchestrationService, create_store
    from deepscientist.orchestration.llm import OrchestrationLLM

    os.environ.pop("OPENAI_API_KEY", None)
    service = OrchestrationService(create_store(tmp_path / "s.db"), llm=OrchestrationLLM())
    obj = service.create_object(name="llm-host", environment="linux")
    result = service.propose_next_action(obj.object_id)
    # Deterministic engine remains authoritative; LLM is optional.
    assert result["ok"] is False
    assert result["available"] is False
    assert "reason" in result
