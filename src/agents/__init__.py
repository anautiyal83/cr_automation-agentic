"""Google ADK agent definitions with liteLLM/OpenRouter integration per FR-034, FR-038."""
from __future__ import annotations

import logging
from typing import Any, Protocol

from src.core.models import RetryContext, StepOutput

logger = logging.getLogger(__name__)

STEP_NAMES = {
    1: "Document Parser",
    2: "UC Analyzer",
    3: "Validation Rules Generator",
    4: "JSON Output Generator",
    5: "CIQ Validation Tester",
    6: "Workflow Generator",
    7: "Simulator Config Generator",
    8: "Execution Tester",
    9: "Final Report Generator",
}

# Steps that use LLM (others are deterministic)
LLM_STEPS = {1, 2, 3, 4, 6, 7}


class AgentStep(Protocol):
    step_number: int
    step_name: str

    async def execute(
        self,
        input_data: dict,
        config: Any,
        few_shot_examples: list[dict] | None = None,
        retry_context: RetryContext | None = None,
    ) -> StepOutput: ...


def get_model_for_step(step_number: int, config: Any) -> str | None:
    if step_number not in LLM_STEPS:
        return None
    step_models = getattr(config, "step_models", {}) if not isinstance(config, dict) else config.get("step_models", {})
    model = step_models.get(step_number)
    if model:
        return model
    default = getattr(config, "default_model", None) if not isinstance(config, dict) else config.get("default_model")
    return default or "openrouter/google/gemini-2.5-flash"


def _resolve_api_key(config: Any = None) -> str:
    """Get OpenRouter API key from config, DB settings, or environment."""
    import os
    # Try config object
    if config:
        key = getattr(config, "openrouter_api_key", None) if not isinstance(config, dict) else config.get("openrouter_api_key")
        if key:
            return key
    # Try environment
    return os.environ.get("OPENROUTER_API_KEY", "")


async def call_llm(
    prompt: str,
    system_prompt: str = "",
    model: str | None = None,
    response_format: dict | None = None,
    config: Any = None,
) -> str:
    """Call LLM via liteLLM/OpenRouter. Raises InfrastructureError on API failures."""
    import os
    import litellm
    from src.core.retry import InfrastructureError

    model = model or "openrouter/google/gemini-2.5-flash"

    # Set API key for liteLLM — OpenRouter uses OPENROUTER_API_KEY env var
    api_key = _resolve_api_key(config)
    if api_key:
        os.environ["OPENROUTER_API_KEY"] = api_key

    messages: list[dict[str, str]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    try:
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if response_format:
            kwargs["response_format"] = response_format
        response = await litellm.acompletion(**kwargs)
        return response.choices[0].message.content or ""
    except Exception as exc:
        err = str(exc).lower()
        if any(kw in err for kw in ("rate", "timeout", "connection", "429", "503", "auth")):
            raise InfrastructureError(f"LLM API error: {exc}") from exc
        raise
