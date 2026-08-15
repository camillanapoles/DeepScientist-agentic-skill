from __future__ import annotations

from deepscientist.orchestration import (
    DecisionEngine,
    EnvironmentRouter,
    create_store,
)
from deepscientist.orchestration.models import (
    ENV_LINUX,
    ENV_MACOS,
    ENV_UNKNOWN,
    ENV_WINDOWS_WSL,
    EVT_CONFIG_COMPLETED,
    EVT_ENVIRONMENT_DETECTED,
    EVT_HUMAN_ACTION_RESOLVED,
    EVT_INSTALL_COMPLETED,
    EVT_PREFLIGHT_RESULT,
    EVT_VALIDATION_RESULT,
    STATE_BLOCKED_HUMAN,
    STATE_CONFIGURING,
    STATE_FAILED,
    STATE_PENDING,
    STATE_PREFLIGHT,
    STATE_PROVISIONING,
    STATE_READY,
    STATE_VALIDATING,
)


def _fresh_object(environment: str = ENV_UNKNOWN) -> dict:
    return {
        "object_id": "obj-test",
        "kind": "agent_host",
        "name": "test-host",
        "environment": environment,
        "state": STATE_PENDING,
        "runner": "",
        "payload": {},
        "created_at": "",
        "updated_at": "",
    }


def _obj(**overrides):
    from deepscientist.orchestration.models import ManagedObject

    data = _fresh_object()
    data.update(overrides)
    return ManagedObject(**data)


def test_linux_environment_routes_to_omp_default() -> None:
    profile = EnvironmentRouter.profile_for("linux")
    assert profile.environment == ENV_LINUX
    assert profile.runner == "omp"
    assert profile.setup_skill == "deepscientist-linux-agent-setup"
    assert profile.package_manager == "apt"


def test_windows_wsl_environment_routes_to_codex() -> None:
    for raw in ("windows", "wsl", "wsl2", "Windows"):
        profile = EnvironmentRouter.profile_for(raw)
        assert profile.environment == ENV_WINDOWS_WSL
        assert profile.runner == "codex"
        assert profile.setup_skill == "deepscientist-windows-wsl-setup"


def test_macos_environment_routes_to_omp() -> None:
    profile = EnvironmentRouter.profile_for("darwin")
    assert profile.environment == ENV_MACOS
    assert profile.runner == "omp"


def test_unknown_environment_is_blocked_not_crashed() -> None:
    engine = DecisionEngine()
    obj = _obj()
    decision = engine.evaluate(obj, EVT_ENVIRONMENT_DETECTED, {"environment": "plan9"})
    assert decision.next_state == STATE_BLOCKED_HUMAN
    assert decision.actions[0]["kind"] == "block_on_human"


def test_full_linux_happy_path_is_deterministic() -> None:
    engine = DecisionEngine()

    # pending -> preflight
    d1 = engine.evaluate(_obj(), EVT_ENVIRONMENT_DETECTED, {"environment": "linux"})
    assert d1.previous_state == STATE_PENDING
    assert d1.next_state == STATE_PREFLIGHT
    assert d1.actions[0]["kind"] == "run_preflight"
    obj = engine.apply_outcome(_obj(), d1, {"environment": "linux"})
    assert obj.environment == ENV_LINUX
    assert obj.runner == "omp"

    # preflight -> provisioning
    d2 = engine.evaluate(obj, EVT_PREFLIGHT_RESULT, {"ok": True})
    assert d2.next_state == STATE_PROVISIONING
    assert d2.actions[0]["kind"] == "install_runtime"
    obj = engine.apply_outcome(obj, d2, {"ok": True})

    # provisioning -> configuring
    d3 = engine.evaluate(obj, EVT_INSTALL_COMPLETED, {"ok": True})
    assert d3.next_state == STATE_CONFIGURING
    obj = engine.apply_outcome(obj, d3, {"ok": True})

    # configuring -> validating
    d4 = engine.evaluate(obj, EVT_CONFIG_COMPLETED, {"ok": True})
    assert d4.next_state == STATE_VALIDATING
    obj = engine.apply_outcome(obj, d4, {"ok": True})

    # validating -> ready
    d5 = engine.evaluate(obj, EVT_VALIDATION_RESULT, {"ok": True})
    assert d5.next_state == STATE_READY
    assert d5.actions[0]["kind"] == "launch"
    obj = engine.apply_outcome(obj, d5, {"ok": True})
    assert obj.state == STATE_READY


def test_preflight_failure_requires_human() -> None:
    engine = DecisionEngine()
    obj = _obj(environment=ENV_LINUX, state=STATE_PREFLIGHT, runner="omp")
    decision = engine.evaluate(
        obj,
        EVT_PREFLIGHT_RESULT,
        {"ok": False, "reason": "no sudo", "requires_human": True, "required_action": "grant sudo"},
    )
    assert decision.next_state == STATE_BLOCKED_HUMAN
    assert any(e["event_type"] == "human.action_required" for e in decision.emitted_events)


def test_install_failure_without_human_flag_fails() -> None:
    engine = DecisionEngine()
    obj = _obj(state=STATE_PROVISIONING, environment=ENV_LINUX, runner="omp")
    decision = engine.evaluate(obj, EVT_INSTALL_COMPLETED, {"ok": False, "reason": "network down"})
    assert decision.next_state == STATE_FAILED


def test_blocked_human_resumes_to_correct_stage() -> None:
    engine = DecisionEngine()
    obj = _obj(state=STATE_BLOCKED_HUMAN, environment=ENV_LINUX, runner="omp")
    decision = engine.evaluate(
        obj,
        EVT_HUMAN_ACTION_RESOLVED,
        {"resume_state": STATE_PROVISIONING},
    )
    assert decision.next_state == STATE_PROVISIONING
    assert decision.actions[0]["kind"] == "install_runtime"


def test_terminal_ready_ignores_further_lifecycle_events() -> None:
    engine = DecisionEngine()
    obj = _obj(state=STATE_READY, environment=ENV_LINUX, runner="omp")
    decision = engine.evaluate(obj, EVT_PREFLIGHT_RESULT, {"ok": True})
    assert decision.next_state == STATE_READY
    assert decision.actions[0]["kind"] == "noop"


def test_unknown_transition_is_noop_never_crash() -> None:
    engine = DecisionEngine()
    obj = _obj(state=STATE_PENDING)
    decision = engine.evaluate(obj, "totally.unknown.event", {})
    assert decision.next_state == STATE_PENDING
    assert decision.actions[0]["kind"] == "noop"


def test_determinism_same_input_same_output() -> None:
    engine = DecisionEngine()
    obj = _obj()
    payload = {"environment": "linux"}
    a = engine.evaluate(obj, EVT_ENVIRONMENT_DETECTED, payload)
    b = engine.evaluate(obj, EVT_ENVIRONMENT_DETECTED, payload)
    # Compare everything except event_ids (which are random).
    assert a.next_state == b.next_state
    assert a.actions == b.actions
    assert a.rationale == b.rationale
    assert [e["event_type"] for e in a.emitted_events] == [
        e["event_type"] for e in b.emitted_events
    ]


def test_runner_override_is_respected() -> None:
    profile = EnvironmentRouter.profile_for("linux", runner_override="codex")
    assert profile.runner == "codex"


# ---- Integration: DB-backed service ----


def test_service_crud_and_event_ingestion_persists_to_db(tmp_path) -> None:
    from deepscientist.orchestration import OrchestrationService

    store = create_store(tmp_path / "state.db")
    service = OrchestrationService(store)

    created = service.create_object(name="linux-box", environment="linux")
    assert created.runner == "omp"
    oid = created.object_id

    # Drive through environment detection first (pending -> preflight).
    service.ingest_event(oid, EVT_ENVIRONMENT_DETECTED, {"environment": "linux"})
    result = service.ingest_event(oid, EVT_PREFLIGHT_RESULT, {"ok": True})
    assert result["decision"]["next_state"] == STATE_PROVISIONING
    assert result["object"]["state"] == STATE_PROVISIONING

    # Persistence: reload from DB via a fresh service.
    store2 = create_store(tmp_path / "state.db")
    service2 = OrchestrationService(store2)
    reloaded = service2.get_object(oid)
    assert reloaded is not None
    assert reloaded.state == STATE_PROVISIONING

    events = service2.get_events(oid)
    decisions = service2.get_decisions(oid)
    assert any(e["event_type"] == EVT_PREFLIGHT_RESULT for e in events)
    assert len(decisions) >= 1
    # At least one emitted follow-up event (install.requested).
    assert any(e["event_type"] == "install.requested" for e in events)


def test_service_list_and_delete(tmp_path) -> None:
    from deepscientist.orchestration import OrchestrationService

    service = OrchestrationService(create_store(tmp_path / "state.db"))
    a = service.create_object(name="a", environment="linux")
    b = service.create_object(name="b", environment="wsl2")

    linux_hosts = service.list_objects(environment=ENV_LINUX)
    assert len(linux_hosts) == 1
    assert linux_hosts[0].object_id == a.object_id

    assert service.delete_object(a.object_id) is True
    assert service.get_object(a.object_id) is None
    assert len(service.list_objects()) == 1


def test_event_bus_publishes_after_persist(tmp_path) -> None:
    from deepscientist.orchestration import OrchestrationService

    received: list[dict] = []
    service = OrchestrationService(create_store(tmp_path / "state.db"))
    service.subscribe("decision.made", lambda e: received.append(e))

    obj = service.create_object(name="bus-host", environment="linux")
    service.ingest_event(obj.object_id, EVT_PREFLIGHT_RESULT, {"ok": True})
    assert any(e["event_type"] == "decision.made" for e in received)


def test_resolve_environment_api_helper(tmp_path) -> None:
    from deepscientist.orchestration import OrchestrationService

    service = OrchestrationService(create_store(tmp_path / "state.db"))
    profile = service.resolve_environment("ubuntu")
    assert profile["runner"] == "omp"
    assert profile["environment"] == "linux"
