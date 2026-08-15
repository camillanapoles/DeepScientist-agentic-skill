from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deepscientist.home import ensure_home_layout  # noqa: E402
from deepscientist.config import ConfigManager  # noqa: E402


@pytest.fixture
def app(temp_home: Path):
    from deepscientist.daemon.app import DaemonApp

    ensure_home_layout(temp_home)
    ConfigManager(temp_home).ensure_files()
    return DaemonApp(temp_home)


def test_daemon_has_orchestration_service(app) -> None:
    assert app.orchestration_service is not None


def test_orchestration_environment_endpoint(app) -> None:
    payload = app.handlers.orchestration_resolve_environment("linux")
    assert payload["ok"] is True
    assert payload["profile"]["runner"] == "omp"
    assert payload["profile"]["environment"] == "linux"

    wsl = app.handlers.orchestration_resolve_environment("wsl2")
    assert wsl["profile"]["runner"] == "codex"
    assert wsl["profile"]["environment"] == "windows_wsl"


def test_orchestration_crud_lifecycle(app) -> None:
    # Create
    created = app.handlers.orchestration_create_object(
        {"name": "api-host", "environment": "linux", "kind": "agent_host"}
    )
    assert created["ok"] is True
    oid = created["object"]["object_id"]
    assert created["object"]["runner"] == "omp"

    # Get
    fetched = app.handlers.orchestration_get_object(oid)
    assert fetched["object"]["name"] == "api-host"

    # List
    listed = app.handlers.orchestration_list_objects()
    assert listed["count"] >= 1
    assert any(o["object_id"] == oid for o in listed["objects"])

    # Update
    updated = app.handlers.orchestration_update_object(oid, {"state": "preflight"})
    assert updated["object"]["state"] == "preflight"

    # Event ingestion drives the state machine.
    result = app.handlers.orchestration_ingest_event(
        oid, {"event_type": "preflight.result", "payload": {"ok": True}}
    )
    assert result["ok"] is True
    assert result["decision"]["next_state"] == "provisioning"
    assert result["object"]["state"] == "provisioning"

    # Events and decisions endpoints.
    events = app.handlers.orchestration_get_events(oid)
    assert events["count"] >= 1
    decisions = app.handlers.orchestration_get_decisions(oid)
    assert decisions["count"] >= 1

    # Delete
    deleted = app.handlers.orchestration_delete_object(oid)
    assert deleted["ok"] is True
    assert app.handlers.orchestration_get_object(oid)["ok"] is False


def test_orchestration_create_requires_name(app) -> None:
    result = app.handlers.orchestration_create_object({"environment": "linux"})
    assert result["ok"] is False
    assert "name" in result["message"].lower()


def test_orchestration_ingest_requires_event_type(app) -> None:
    created = app.handlers.orchestration_create_object(
        {"name": "evt-host", "environment": "linux"}
    )
    oid = created["object"]["object_id"]
    result = app.handlers.orchestration_ingest_event(oid, {"payload": {}})
    assert result["ok"] is False
    assert "event_type" in result["message"].lower()


def test_orchestration_full_linux_flow_via_api(app) -> None:
    """Drive an object all the way to ready through the API layer."""
    created = app.handlers.orchestration_create_object(
        {"name": "ready-host", "environment": "linux"}
    )
    oid = created["object"]["object_id"]

    # pending -> preflight
    r = app.handlers.orchestration_ingest_event(
        oid, {"event_type": "environment.detected", "payload": {"environment": "linux"}}
    )
    assert r["decision"]["next_state"] == "preflight"

    # preflight -> provisioning
    r = app.handlers.orchestration_ingest_event(
        oid, {"event_type": "preflight.result", "payload": {"ok": True}}
    )
    assert r["decision"]["next_state"] == "provisioning"

    # provisioning -> configuring
    r = app.handlers.orchestration_ingest_event(
        oid, {"event_type": "install.completed", "payload": {"ok": True}}
    )
    assert r["decision"]["next_state"] == "configuring"

    # configuring -> validating
    r = app.handlers.orchestration_ingest_event(
        oid, {"event_type": "config.completed", "payload": {"ok": True}}
    )
    assert r["decision"]["next_state"] == "validating"

    # validating -> ready
    r = app.handlers.orchestration_ingest_event(
        oid, {"event_type": "validation.result", "payload": {"ok": True}}
    )
    assert r["decision"]["next_state"] == "ready"
    assert any(a["kind"] == "launch" for a in r["decision"]["actions"])
