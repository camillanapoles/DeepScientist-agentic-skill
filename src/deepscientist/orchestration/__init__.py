"""Event-driven deterministic orchestration for DeepScientist."""
from __future__ import annotations

from .agent import InternalValidationAgent, run_merge_gate
from .db import OrchestrationStore, create_store
from .decisions import (
    Decision,
    DecisionEngine,
    EnvironmentRouter,
    ManagedObject,
)
from .events import EventBus, build_event
from .llm import OrchestrationLLM
from .schemas import (
    Action,
    ClosedLoopVerdict,
    Environment,
    EnvironmentProfile,
    EventDrivenPlaybook,
    InstructorActionProposal,
    ObjectState,
    OrchestrationEvent,
)
from .service import OrchestrationService

__all__ = [
    "Action",
    "ClosedLoopVerdict",
    "Decision",
    "DecisionEngine",
    "Environment",
    "EnvironmentProfile",
    "EnvironmentRouter",
    "EventBus",
    "EventDrivenPlaybook",
    "InstructorActionProposal",
    "InternalValidationAgent",
    "ManagedObject",
    "ObjectState",
    "OrchestrationEvent",
    "OrchestrationLLM",
    "OrchestrationService",
    "OrchestrationStore",
    "build_event",
    "create_store",
]
