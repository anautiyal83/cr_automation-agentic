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
    4: "JSON Template Generator",
    5: "Workflow Generator",
    6: "Script + Simulator Config Generator",
    7: "Schema Validator",
    8: "Test Executor",
    9: "Report Generator",
}


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
    if step_number > 6:
        return None
    step_models = getattr(config, "step_models", {}) if not isinstance(config, dict) else config.get("step_models", {})
    model = step_models.get(step_number)
    if model:
        return model
    default = getattr(config, "default_model", None) if not isinstance(config, dict) else config.get("default_model")
    return default or "openrouter/anthropic/claude-sonnet-4-20250514"


async def call_llm(
    prompt: str,
    system_prompt: str = "",
    model: str | None = None,
    response_format: dict | None = None,
) -> str:
    """Call LLM via liteLLM/OpenRouter. Raises InfrastructureError on API failures."""
    import litellm
    from src.core.retry import InfrastructureError

    model = model or "openrouter/anthropic/claude-sonnet-4-20250514"
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
        if any(kw in err for kw in ("rate", "timeout", "connection", "429", "503")):
            raise InfrastructureError(f"LLM API error: {exc}") from exc
        raise
