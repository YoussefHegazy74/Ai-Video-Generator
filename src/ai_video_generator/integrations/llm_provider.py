"""Provider-agnostic LLM boundary and the implemented Gemini adapter.

Responsibility: define the stable interface consumed by planning agents and expose
the configured production adapter without leaking SDK details into agent code.
Workflow: agents construct ``LLMRequest`` values; ``GeminiLLMClient`` sends them to
Gemini with structured output and returns ``LLMResponse`` objects.
Inputs: prompts, JSON Schema, temperature, and provider configuration.
Outputs: validated JSON text plus provider/model metadata or typed errors.
Interacts with: ``agents.scene_planning.llm``, ``gemini_llm_client``, core
settings/exceptions, and the optional ``google-genai`` SDK.

Only Gemini is implemented. Adding another provider means adding a new class that
satisfies ``LLMClient`` and wiring it in ``provider_factory.build_llm_client``;
agents must not change.
"""

from ai_video_generator.agents.scene_planning.llm import (
    LLMClient,
    LLMRequest,
    LLMResponse,
)
from ai_video_generator.integrations.gemini_llm_client import (
    DEFAULT_MODEL,
    PROVIDER_ID,
    GeminiLLMClient,
)

__all__ = [
    "DEFAULT_MODEL",
    "GeminiLLMClient",
    "LLMClient",
    "LLMRequest",
    "LLMResponse",
    "PROVIDER_ID",
]
