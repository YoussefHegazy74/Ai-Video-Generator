"""Scene-planning agent support package.

Responsibility: shared LLM boundary, prompt/schema definitions, errors, and
validation for the Script Agent and Whiteboard Director Agent.
Workflow: imported by both agents; not a runtime workflow component itself.
Inputs: structured scene-planning inputs and LLM JSON responses.
Outputs: validated contracts and validation reports.
Interacts with: ``agents.scripting``, ``agents.whiteboard``, ``models``, and
``whiteboard`` contracts.
"""

from .errors import (
    AgentInputError,
    AgentOutputError,
    LLMProviderError,
    LLMResponseFormatError,
    ScenePlanningError,
)
from .llm import LLMClient, LLMRequest, LLMResponse, parse_json_object
from .validation import (
    IssueSeverity,
    ValidationIssue,
    ValidationReport,
    validate_planning_input,
    validate_script,
    validate_whiteboard,
)

__all__ = [
    "AgentInputError",
    "AgentOutputError",
    "IssueSeverity",
    "LLMClient",
    "LLMProviderError",
    "LLMRequest",
    "LLMResponse",
    "LLMResponseFormatError",
    "ScenePlanningError",
    "ValidationIssue",
    "ValidationReport",
    "parse_json_object",
    "validate_planning_input",
    "validate_script",
    "validate_whiteboard",
]
