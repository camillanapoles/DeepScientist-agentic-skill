"""Instructor-wrapped LLM client for orchestration decisions.

All model-assisted decisions return a Pydantic model — never free-form
text. Instructor handles response_model enforcement, native retries, and
schema validation. The orchestration service then applies an additional
**imperative** validation against the DB state before any mutation.

The client is defensive: if the ``instructor`` package or an API key is
unavailable, :meth:`OrchestrationLLM.propose_action` returns a structured
:class:`ProposalUnavailable` result rather than raising, so the
deterministic engine remains the source of truth.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, TypeVar

from pydantic import BaseModel

from .schemas import (
    Environment,
    EnvironmentDetectionResult,
    EnvironmentProfile,
    InstructorActionProposal,
    ManagedObject,
)

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

#: Canonical environment/runner routing table — the LLM may not override this.
_ENV_ROUTING: dict[Environment, EnvironmentProfile] = {
    Environment.LINUX: EnvironmentProfile(
        environment=Environment.LINUX,
        runner="omp",
        setup_skill="deepscientist-linux-agent-setup",
        package_manager="apt",
        notes="Native Linux user environment; OMP/pi-coding-agent is the default runner.",
    ),
    Environment.WINDOWS_WSL: EnvironmentProfile(
        environment=Environment.WINDOWS_WSL,
        runner="codex",
        setup_skill="deepscientist-windows-wsl-setup",
        package_manager="apt-in-wsl",
        notes="Windows host + WSL2; run wsl.exe from Windows and apt inside the distro.",
    ),
    Environment.MACOS: EnvironmentProfile(
        environment=Environment.MACOS,
        runner="omp",
        setup_skill="deepscientist-linux-agent-setup",
        package_manager="brew",
    ),
    Environment.UNKNOWN: EnvironmentProfile(
        environment=Environment.UNKNOWN,
        runner="omp",
        setup_skill="deepscientist-linux-agent-setup",
        package_manager="unknown",
        notes="Environment is unknown; run detection before provisioning.",
    ),
}


@dataclass(frozen=True)
class ProposalUnavailable:
    """Returned when the LLM/proposal path is not available.

    The deterministic engine should be used instead — this never blocks
    orchestration.
    """

    reason: str


# ---------------------------------------------------------------------------
# Prompt builders — system + user prompts are constructed from typed objects.
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are the orchestration assistant for DeepScientist. "
    "You propose the next event in a deterministic state machine. "
    "You MUST return a structured object matching the required schema. "
    "You must never invent an environment, runner, or state that is not "
    "supported. The current object state and transition table are authoritative. "
    "When unsure or when a human action is required, set needs_human=true and "
    "provide the required action. Long-horizon rule (OSWorld 2.0): propose "
    "exactly one atomic next step; the caller validates progress imperatively "
    "before advancing."
)


def _build_proposal_prompt(obj: ManagedObject, last_rationale: str | None) -> str:
    lines = [
        f"Managed object: {obj.name} ({obj.object_id})",
        f"Environment: {obj.environment.value}",
        f"Current state: {obj.state.value}",
        f"Runner: {obj.runner or '(unset)'}",
    ]
    if obj.payload:
        lines.append(f"Payload keys: {', '.join(sorted(obj.payload))}")
    if last_rationale:
        lines.append(f"Last decision rationale: {last_rationale}")
    lines.append("")
    lines.append(
        "Propose the single next event to ingest. Valid event types are: "
        "environment.detected, preflight.result, install.completed, "
        "config.completed, validation.result, human.action_required, "
        "human.action_resolved. For result events, set payload.ok to true/false."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class OrchestrationLLM:
    """Thin wrapper around Instructor for structured orchestration proposals."""

    #: Default provider is ZAI (z.ai coding plan, OpenAI-compatible wire API).
    #: Set DS_ORCHESTRATION_PROVIDER=openai to use OpenAI directly.
    ZAI_BASE_URL = "https://api.z.ai/api/coding/paas/v4"
    DEFAULT_PROVIDER = "zai"
    DEFAULT_MODEL = "glm-5.2"

    def __init__(
        self,
        *,
        model: str | None = None,
        provider: str | None = None,
        base_url: str | None = None,
        max_retries: int = 3,
    ) -> None:
        self.model = model or os.environ.get("DS_ORCHESTRATION_MODEL", self.DEFAULT_MODEL)
        self.provider = (
            provider or os.environ.get("DS_ORCHESTRATION_PROVIDER", self.DEFAULT_PROVIDER)
        ).strip().lower()
        self.base_url = (
            base_url
            or os.environ.get("DS_ORCHESTRATION_BASE_URL")
            or (self.ZAI_BASE_URL if self.provider == "zai" else os.environ.get("OPENAI_BASE_URL", ""))
        ).strip()
        self.max_retries = max_retries
        self._client: Any | None = None
        self._init_error: str | None = None

    # ------------------------------------------------------------------ setup

    def _api_key(self) -> str | None:
        """Resolve the API key for the active provider."""
        if self.provider == "zai":
            return os.environ.get("ZAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        return os.environ.get("OPENAI_API_KEY")

    def _ensure_client(self) -> Any | None:
        if self._client is not None:
            return self._client
        if self._init_error is not None:
            return None
        try:
            import instructor  # type: ignore
            from openai import OpenAI  # type: ignore
        except ImportError as exc:
            self._init_error = f"instructor/openai not installed: {exc}"
            logger.warning("Orchestration LLM unavailable: %s", self._init_error)
            return None
        api_key = self._api_key()
        if not api_key:
            self._init_error = (
                f"no API key for provider {self.provider!r} "
                "(set ZAI_API_KEY for the z.ai coding plan, or OPENAI_API_KEY)"
            )
            logger.warning("Orchestration LLM unavailable: %s", self._init_error)
            return None
        try:
            base_client = OpenAI(
                api_key=api_key,
                base_url=self.base_url or None,
            )
            self._client = instructor.from_openai(
                base_client,
                mode=instructor.Mode.JSON,
            )
        except Exception as exc:  # noqa: BLE001
            self._init_error = f"failed to initialise instructor client: {exc}"
            logger.warning("%s", self._init_error)
            self._client = None
        return self._client

    @property
    def available(self) -> bool:
        return self._ensure_client() is not None

    # ------------------------------------------------------------------ routing

    @staticmethod
    def profile_for(environment: Environment | str) -> EnvironmentProfile:
        env = environment if isinstance(environment, Environment) else Environment(environment)
        return _ENV_ROUTING[env]

    # ------------------------------------------------------------------ API

    def detect_environment(
        self, evidence: str
    ) -> EnvironmentDetectionResult | ProposalUnavailable:
        """Classify a target environment from free-form evidence text.

        The routing table is authoritative; the LLM may not override the
        runner/setup-skill mapping.
        """
        if not self.available:
            return ProposalUnavailable(self._init_error or "LLM unavailable")
        client = self._ensure_client()
        try:
            result = client.chat.completions.create(  # type: ignore[union-attr]
                model=self.model,
                response_model=EnvironmentDetectionResult,
                max_retries=self.max_retries,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            "Classify the target environment from this evidence "
                            f"and choose the correct runner/skill:\n\n{evidence[:4000]}"
                        ),
                    },
                ],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Environment detection LLM call failed: %s", exc)
            return ProposalUnavailable(str(exc))
        # Re-assert the authoritative routing for the detected environment.
        authoritative = self.profile_for(result.environment)
        return EnvironmentDetectionResult(
            environment=authoritative.environment,
            runner=authoritative.runner,
            setup_skill=authoritative.setup_skill,
            reasoning=result.reasoning,
        )

    def propose_action(
        self,
        obj: ManagedObject,
        *,
        last_rationale: str | None = None,
    ) -> InstructorActionProposal | ProposalUnavailable:
        """Propose the next structured action for a managed object."""
        if not self.available:
            return ProposalUnavailable(self._init_error or "LLM unavailable")
        client = self._ensure_client()
        try:
            proposal = client.chat.completions.create(  # type: ignore[union-attr]
                model=self.model,
                response_model=InstructorActionProposal,
                max_retries=self.max_retries,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": _build_proposal_prompt(obj, last_rationale),
                    },
                ],
            )
        except ValidationError as exc:
            return ProposalUnavailable(f"LLM returned invalid structure: {exc}")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Action proposal LLM call failed: %s", exc)
            return ProposalUnavailable(str(exc))
        return self._validate_proposal(proposal, obj)

    # ------------------------------------------------------------------ gate

    @staticmethod
    def _validate_proposal(
        proposal: InstructorActionProposal, obj: ManagedObject
    ) -> InstructorActionProposal:
        """Imperative sanity check before the caller applies the proposal."""
        # A blocked-human proposal must carry a required action.
        if proposal.needs_human and not proposal.required_human_action:
            raise ValueError(
                "needs_human=true but required_human_action is missing"
            )
        # Result events must carry an `ok` boolean in their payload.
        if proposal.proposed_event_type.value.endswith(".result"):
            if "ok" not in proposal.payload:
                proposal.payload["ok"] = False
        return proposal
