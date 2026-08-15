"""Orchestration service: CRUD + event-driven deterministic decisions.

This is the single entry point used by the daemon API. It owns a
:class:`OrchestrationStore` (DB = source of truth), a
:class:`DecisionEngine` (pure state machine) and an :class:`EventBus`
(notifications). Every mutation goes through here so the DB, decision
log and bus stay consistent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..shared import generate_id, utc_now
from .db import OrchestrationStore
from .decisions import DecisionEngine, EnvironmentRouter
from .events import EventBus, build_event
from .llm import OrchestrationLLM, ProposalUnavailable
from .models import (
    ACTION_NOOP,
    ENV_UNKNOWN,
    EVT_ENVIRONMENT_DETECTED,
    KIND_AGENT_HOST,
    STATE_PENDING,
    Decision,
    ManagedObject,
)
from .schemas import (
    Action as TypedAction,
    ActionKind,
    Decision as TypedDecision,
    Environment,
    EnvironmentProfile,
    EventDrivenPlaybook,
    ManagedObject as TypedManagedObject,
    ObjectState,
    OrchestrationEvent,
)


class OrchestrationService:
    def __init__(
        self,
        store: OrchestrationStore,
        *,
        bus: EventBus | None = None,
        engine: DecisionEngine | None = None,
        llm: OrchestrationLLM | None = None,
    ) -> None:
        self.store = store
        self.bus = bus or EventBus()
        self.engine = engine or DecisionEngine()
        self.llm = llm or OrchestrationLLM()

    # ------------------------------------------------------------------ factory

    @classmethod
    def for_home(cls, home) -> "OrchestrationService":
        home_path = Path(home)
        store = OrchestrationStore(home_path / "orchestration" / "state.db")
        return cls(store)

    # ------------------------------------------------------------------ CRUD

    def create_object(
        self,
        *,
        kind: str = KIND_AGENT_HOST,
        name: str,
        environment: str = ENV_UNKNOWN,
        runner: str = "",
        payload: dict[str, Any] | None = None,
        object_id: str | None = None,
    ) -> ManagedObject:
        env = EnvironmentRouter.normalize_environment(environment)
        if not runner and env != ENV_UNKNOWN:
            profile = EnvironmentRouter.profile_for(env, object_payload=payload or {})
            runner = profile.runner
        obj = self.store.create_object(
            kind=kind,
            name=name,
            environment=env,
            state=STATE_PENDING,
            runner=runner,
            payload=payload or {},
            object_id=object_id,
        )
        self._publish(
            "object.created",
            {"object_id": obj.object_id, "kind": obj.kind, "environment": obj.environment},
            obj.object_id,
        )
        return obj

    def get_object(self, object_id: str) -> ManagedObject | None:
        return self.store.get_object(object_id)

    def list_objects(self, **filters: Any) -> list[ManagedObject]:
        return self.store.list_objects(**filters)

    def update_object(self, object_id: str, **fields: Any) -> ManagedObject | None:
        payload_patch = fields.pop("payload", None)
        obj = self.store.update_object(object_id, payload_patch=payload_patch, **fields)
        if obj is not None:
            self._publish(
                "object.updated",
                {"object_id": object_id, "state": obj.state, "environment": obj.environment},
                object_id,
            )
        return obj

    def delete_object(self, object_id: str) -> bool:
        deleted = self.store.delete_object(object_id)
        if deleted:
            self._publish("object.deleted", {"object_id": object_id}, object_id)
        return deleted

    # ------------------------------------------------------------------ events

    def ingest_event(
        self,
        object_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Process one event through the deterministic engine.

        Returns a dict with the resulting decision and the updated
        object. The event is appended to the DB *before* the engine
        runs, so input events are always durable even if the decision
        is a no-op.
        """
        data = payload or {}
        obj = self.store.get_object(object_id)
        if obj is None:
            raise KeyError(f"Unknown object_id: {object_id}")

        # Persist the inbound event first (durable input log).
        inbound_event_id = self.store.append_event(object_id, event_type, data)

        # Pure decision.
        decision = self.engine.evaluate(obj, event_type, data)

        # Apply the outcome to the DB transactionally.
        self._persist_decision(obj, decision, data)

        # Record the decision audit row.
        self.store.record_decision(
            object_id=object_id,
            event_id=inbound_event_id,
            previous_state=decision.previous_state,
            next_state=decision.next_state,
            rationale=decision.rationale,
            actions=decision.actions,
        )

        # Publish outbound emitted events + a decision event.
        for emitted in decision.emitted_events:
            # Emit to bus; the DB event row was already created by _persist_decision.
            self.bus.publish(emitted)
        self.bus.publish(
            build_event(
                "decision.made",
                object_id=object_id,
                payload={
                    "event_type": event_type,
                    "previous_state": decision.previous_state,
                    "next_state": decision.next_state,
                    "actions": [dict(a) for a in decision.actions],
                    "rationale": decision.rationale,
                },
            )
        )

        updated = self.store.get_object(object_id)
        return {
            "object": updated.to_dict() if updated else None,
            "decision": decision.to_dict(),
        }

    def _persist_decision(
        self, obj: ManagedObject, decision: Decision, original_payload: dict[str, Any]
    ) -> None:
        patch: dict[str, Any] = {}
        # Environment/runner may be set by the engine via environment detection.
        if decision.next_state != decision.previous_state or decision.emitted_events:
            # Re-derive environment/runner from the relevant event.
            for emitted in decision.emitted_events:
                if emitted["event_type"] == EVT_ENVIRONMENT_DETECTED:
                    env = EnvironmentRouter.normalize_environment(
                        emitted.get("payload", {}).get("environment")
                    )
                    if env != ENV_UNKNOWN:
                        patch["environment"] = env
                        profile = EnvironmentRouter.profile_for(env, object_payload=obj.payload)
                        patch["runner"] = profile.runner

        # Persist state transition.
        payload_patch = dict(original_payload) if decision.next_state in {
            "provisioning",
            "configuring",
            "validating",
            "ready",
            "failed",
            "blocked_human",
        } else {}

        # Normalise any nested result payloads so they survive JSON round-trip.
        for key in ("preflight", "install", "config", "validation"):
            if key in payload_patch and not isinstance(payload_patch[key], dict):
                payload_patch[key] = {"value": payload_patch[key]}

        self.store.update_object(
            obj.object_id,
            state=decision.next_state if decision.next_state != decision.previous_state else None,
            environment=patch.get("environment"),
            runner=patch.get("runner"),
            payload_patch=payload_patch or None,
        )

        # Persist emitted events.
        for emitted in decision.emitted_events:
            self.store.append_event(
                obj.object_id,
                emitted["event_type"],
                emitted.get("payload", {}),
            )

    # ------------------------------------------------------------------ queries

    def get_events(self, object_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
        return self.store.list_events(object_id, limit=limit)

    def get_decisions(self, object_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.store.list_decisions(object_id, limit=limit)

    def resolve_environment(self, environment: str, **kwargs: Any) -> dict[str, Any]:
        """API helper: resolve the profile for an environment string."""
        return EnvironmentRouter.profile_for(environment, **kwargs).to_dict()

    # ------------------------------------------------------------------ bus

    def subscribe(self, event_type: str | None, handler) -> Any:
        return self.bus.subscribe(event_type, handler)

    def _publish(self, event_type: str, payload: dict[str, Any], object_id: str | None) -> None:
        self.bus.publish(
            build_event(event_type, object_id=object_id, payload=payload)
        )

    # ------------------------------------------------------------------ typed projection

    def _to_typed_object(self, obj: ManagedObject) -> TypedManagedObject:
        from datetime import datetime

        def _parse(value: str | None) -> datetime | None:
            if not value:
                return None
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None

        return TypedManagedObject(
            object_id=obj.object_id,
            kind=obj.kind,
            name=obj.name,
            environment=Environment(obj.environment),
            state=ObjectState(obj.state),
            runner=obj.runner,
            payload=dict(obj.payload),
            created_at=_parse(obj.created_at),
            updated_at=_parse(obj.updated_at),
        )

    def render_playbook(self, object_id: str) -> dict[str, Any]:
        """Render the deterministic, Pydantic-typed playbook for an object."""
        obj = self.store.get_object(object_id)
        if obj is None:
            raise KeyError(f"Unknown object_id: {object_id}")
        latest = self.store.latest_decision(object_id)
        decisions = [latest] if latest else []
        events = self.store.list_events(object_id, limit=10)
        profile = EnvironmentRouter.profile_for(obj.environment, object_payload=obj.payload)

        if decisions:
            last = decisions[-1]
            typed_actions = [
                TypedAction(
                    kind=ActionKind(str(a.get("kind") or "noop")),
                    reason=a.get("reason"),
                    required_action=a.get("required_action"),
                    runner=a.get("runner"),
                    environment=a.get("environment"),
                    setup_skill=a.get("setup_skill"),
                )
                for a in last.get("actions", [])
                if isinstance(a, dict)
            ]
            from datetime import datetime

            typed_events = [
                OrchestrationEvent(
                    event_id=str(e.get("event_id") or ""),
                    event_type=str(e.get("event_type") or ""),
                    object_id=e.get("quest_id"),
                    created_at=datetime.fromisoformat(str(e["created_at"]).replace("Z", "+00:00")),
                    payload=e.get("payload") if isinstance(e.get("payload"), dict) else {},
                )
                for e in events
                if isinstance(e, dict) and e.get("created_at")
            ]
            decision = TypedDecision(
                object_id=object_id,
                previous_state=ObjectState(last["previous_state"]),
                next_state=ObjectState(last["next_state"]),
                rationale=str(last.get("rationale") or ""),
                actions=typed_actions,
                emitted_events=[],
            )
        else:
            decision = TypedDecision(
                object_id=object_id,
                previous_state=ObjectState(obj.state),
                next_state=ObjectState(obj.state),
                rationale="No decision has been recorded yet.",
                actions=[],
                emitted_events=[],
            )
            typed_events = []

        typed_profile = EnvironmentProfile(
            environment=Environment(profile.environment),
            runner=profile.runner,
            setup_skill=profile.setup_skill,
            package_manager=profile.package_manager,
            notes=profile.notes,
        )
        playbook = EventDrivenPlaybook(
            managed_object=self._to_typed_object(obj),
            decision=decision,
            profile=typed_profile,
            recent_events=typed_events,
        )
        verdict = playbook.closed_loop_verdict()
        return {
            "ok": True,
            "markdown": playbook.render_markdown(),
            "verdict": verdict.model_dump(mode="json"),
            "object": obj.to_dict(),
        }

    # ------------------------------------------------------------------ LLM-assisted proposal

    def propose_next_action(self, object_id: str) -> dict[str, Any]:
        """Ask the Instructor-wrapped LLM for the next event.

        The proposal is **never applied automatically** — the caller
        posts it through :meth:`ingest_event` for deterministic
        validation.
        """
        obj = self.store.get_object(object_id)
        if obj is None:
            raise KeyError(f"Unknown object_id: {object_id}")
        last_rationale = None
        latest = self.store.latest_decision(object_id)
        decisions = [latest] if latest else []
        if decisions:
            last_rationale = str(decisions[-1].get("rationale") or "")
        proposal = self.llm.propose_action(
            self._to_typed_object(obj), last_rationale=last_rationale
        )
        if isinstance(proposal, ProposalUnavailable):
            return {"ok": False, "available": False, "reason": proposal.reason}
        return {"ok": True, "available": True, "proposal": proposal.model_dump(mode="json")}

    def detect_environment(self, evidence: str) -> dict[str, Any]:
        """LLM-assisted environment classification (structurally validated)."""
        result = self.llm.detect_environment(evidence)
        if isinstance(result, ProposalUnavailable):
            return {"ok": False, "available": False, "reason": result.reason}
        return {"ok": True, "available": True, "result": result.model_dump(mode="json")}
