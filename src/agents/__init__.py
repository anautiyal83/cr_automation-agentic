"""Google ADK agent definitions with liteLLM/OpenRouter integration per FR-034, FR-038."""
from __future__ import annotations

import logging
import time as _time_mod
from typing import Any, Protocol

from src.core.models import RetryContext, StepOutput

logger = logging.getLogger(__name__)

# ── LLM Usage Tracking ──────────────────────────────────────────────────────
# Shared list of all LLM calls — read by the Usage UI page
llm_usage_log: list[dict] = []


def _track_llm_usage(model: str, prompt_tokens: int, completion_tokens: int,
                      total_tokens: int, elapsed_sec: float, prompt_chars: int):
    """Record a single LLM call for usage stats."""
    llm_usage_log.append({
        "timestamp": _time_mod.time(),
        "model": model,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "elapsed_sec": round(elapsed_sec, 2),
        "prompt_chars": prompt_chars,
    })


def get_usage_stats() -> dict:
    """Aggregate usage stats across all LLM calls."""
    if not llm_usage_log:
        return {"total_calls": 0, "total_tokens": 0, "total_prompt": 0,
                "total_completion": 0, "total_time": 0, "by_model": {}}

    total_calls = len(llm_usage_log)
    total_tokens = sum(e["total_tokens"] for e in llm_usage_log)
    total_prompt = sum(e["prompt_tokens"] for e in llm_usage_log)
    total_completion = sum(e["completion_tokens"] for e in llm_usage_log)
    total_time = sum(e["elapsed_sec"] for e in llm_usage_log)

    by_model: dict[str, dict] = {}
    for e in llm_usage_log:
        m = e["model"]
        if m not in by_model:
            by_model[m] = {"calls": 0, "tokens": 0, "prompt": 0, "completion": 0, "time": 0}
        by_model[m]["calls"] += 1
        by_model[m]["tokens"] += e["total_tokens"]
        by_model[m]["prompt"] += e["prompt_tokens"]
        by_model[m]["completion"] += e["completion_tokens"]
        by_model[m]["time"] += e["elapsed_sec"]

    return {
        "total_calls": total_calls,
        "total_tokens": total_tokens,
        "total_prompt": total_prompt,
        "total_completion": total_completion,
        "total_time": round(total_time, 1),
        "by_model": by_model,
        "calls": llm_usage_log,
    }

STEP_NAMES = {
    1: "Document Parser",
    2: "UC Analyzer",
    3: "Validation Rules Generator",
    4: "JSON Output Generator",
    5: "CIQ Validation Tester",
    6: "Workflow Generator",
    7: "MOP Generation Tester",
    8: "Simulator Config Generator",
    9: "Execution Tester",
    10: "Final Report Generator",
}

# Steps that use LLM (others are deterministic)
LLM_STEPS = {1, 2, 3, 4, 6, 8}


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

    prompt_chars = len(prompt) + len(system_prompt)
    logger.info("LLM call: model=%s, prompt_size=%d chars", model, prompt_chars)

    try:
        import time as _time
        t0 = _time.monotonic()
        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if response_format:
            kwargs["response_format"] = response_format
        response = await litellm.acompletion(**kwargs)
        elapsed = _time.monotonic() - t0
        content = response.choices[0].message.content or ""
        usage = getattr(response, "usage", None)
        token_info = ""
        prompt_tok = 0
        comp_tok = 0
        total_tok = 0
        if usage:
            prompt_tok = getattr(usage, "prompt_tokens", 0) or 0
            comp_tok = getattr(usage, "completion_tokens", 0) or 0
            total_tok = getattr(usage, "total_tokens", 0) or 0
            token_info = f", tokens={total_tok} (prompt={prompt_tok}, completion={comp_tok})"
        logger.info("LLM RESPONSE model=%s — %d chars in %.1fs%s", model, len(content), elapsed, token_info)

        # Track usage for stats
        _track_llm_usage(model, prompt_tok, comp_tok, total_tok, elapsed, prompt_chars)

        return content
    except Exception as exc:
        err = str(exc).lower()
        logger.error("LLM CALL FAILED model=%s — %s", model, exc)
        if any(kw in err for kw in ("rate", "timeout", "connection", "429", "503", "auth")):
            raise InfrastructureError(f"LLM API error: {exc}") from exc
        raise
