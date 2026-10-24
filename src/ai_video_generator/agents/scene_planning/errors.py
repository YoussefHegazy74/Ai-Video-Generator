"""Errors raised by the scene-planning agents.

Responsibility: provide typed failures for invalid inputs, provider failures,
malformed structured output, and contract validation failures.
Workflow: shared by the Script Agent and Whiteboard Director Agent.
Inputs: error messages, validation details, and raw model output for diagnostics.
Outputs: catchable exceptions for orchestration and tests.
Interacts with: both agents, ``llm.py``, ``validation.py``, and future
orchestration retry policy.
"""

from __future__ import annotations

from typing import Sequence


class ScenePlanningError(Exception):
    """Base class for scene-planning failures."""

    def __init__(self, message: str, *, details: Sequence[str] = ()) -> None:
        super().__init__(message)
        self.details = tuple(details)


class AgentInputError(ScenePlanningError):
    """Raised when upstream scene-planning input is inconsistent or incomplete."""


class LLMProviderError(ScenePlanningError):
    """Raised when a provider call fails before returning usable output."""


class LLMResponseFormatError(ScenePlanningError):
    """Raised when a provider response is not a single JSON object."""


class AgentOutputError(ScenePlanningError):
    """Raised when structured agent output violates its contract."""

    def __init__(
        self,
        agent_name: str,
        message: str,
        *,
        details: Sequence[str] = (),
        raw_output: str | None = None,
    ) -> None:
        super().__init__(f"{agent_name}: {message}", details=details)
        self.agent_name = agent_name
        self.raw_output = raw_output


__all__ = [
    "AgentInputError",
    "AgentOutputError",
    "LLMProviderError",
    "LLMResponseFormatError",
    "ScenePlanningError",
]
