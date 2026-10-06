"""
Configuration manager for the CR Automation pipeline.

Loads configuration from YAML files, supports DB-stored overrides,
per-step model assignments, and API key masking.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from ruamel.yaml import YAML

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Project root: two levels up from this file  (src/core/config.py -> project root)
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PIPELINE_CONFIG_PATH = PROJECT_ROOT / "config" / "pipeline_config.yaml"
MODELS_CONFIG_PATH = PROJECT_ROOT / "config" / "models.yaml"


# ---------------------------------------------------------------------------
# Utility: API key masking
# ---------------------------------------------------------------------------

def mask_api_key(key: str) -> str:
    """Return a masked version of an API key, showing only the last 4 chars.

    Format: ``sk-...xxxx``  (last 4 characters of the original key).
    Returns an empty string unchanged.
    """
    if not key or len(key) <= 4:
        return key
    return f"sk-...{key[-4:]}"


# ---------------------------------------------------------------------------
# ModelInfo dataclass
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """Metadata about an available LLM model."""

    model_id: str
    display_name: str
    context_window: int
    cost_per_1k_tokens: float


# ---------------------------------------------------------------------------
# Pydantic configuration model
# ---------------------------------------------------------------------------

class PipelineConfiguration(BaseModel):
    """Validated pipeline configuration."""

    openrouter_api_key: str = ""
    default_model: str = "openrouter/anthropic/claude-sonnet-4-20250514"
    step_models: dict[int, str | None] = Field(default_factory=dict)
    test_retry_limit: int = Field(default=3, ge=1, le=10)
    infra_retry_limit: int = Field(default=3, ge=1, le=10)
    infra_retry_backoff: float = Field(default=2.0)
    simulator_endpoint: str = "localhost"
    simulator_port: int = 8080
    ciq_processor_jar: str = "./lib/ciq-processor.jar"
    cli_automation_jar: str = "./lib/cli-automation-standalone.jar"
    output_directory: str = "./data/runs"
    chunk_size_rows: int = Field(default=100)
    few_shot_count: int = Field(default=3)
    database_path: str = "./data/pipeline.db"
    knowledge_base_directory: str = "./data/knowledge"

    # -- validators ----------------------------------------------------------

    @field_validator("openrouter_api_key", mode="before")
    @classmethod
    def _resolve_env_var(cls, v: str) -> str:
        """If the value looks like ``${ENV_VAR}``, resolve from the environment."""
        if isinstance(v, str):
            match = re.fullmatch(r"\$\{(\w+)\}", v)
            if match:
                return os.environ.get(match.group(1), "")
        return v if v is not None else ""

    # -- display helpers -----------------------------------------------------

    def masked_dict(self) -> dict[str, Any]:
        """Return a dict safe for display / logging (API key masked)."""
        d = self.model_dump()
        d["openrouter_api_key"] = mask_api_key(d.get("openrouter_api_key", ""))
        return d

    def __repr__(self) -> str:  # pragma: no cover
        return f"PipelineConfiguration({self.masked_dict()})"


# ---------------------------------------------------------------------------
# ConfigManager
# ---------------------------------------------------------------------------

class ConfigManager:
    """Async configuration manager.

    * Loads base config from ``config/pipeline_config.yaml``
    * Merges per-step model assignments from ``config/models.yaml``
    * Overlays DB-stored overrides (when a DB accessor is provided)
    * Never logs or displays raw API keys
    """

    def __init__(
        self,
        config_path: Path | str | None = None,
        models_path: Path | str | None = None,
        db_accessor: Any | None = None,
    ) -> None:
        self._config_path = Path(config_path) if config_path else PIPELINE_CONFIG_PATH
        self._models_path = Path(models_path) if models_path else MODELS_CONFIG_PATH
        self._db_accessor = db_accessor
        self._yaml = YAML()
        self._yaml.preserve_quotes = True
        self._config: PipelineConfiguration | None = None

    # -- internal helpers ----------------------------------------------------

    def _load_yaml(self, path: Path) -> dict[str, Any]:
        """Read a YAML file and return its contents as a dict."""
        if not path.exists():
            logger.warning("Config file not found: %s", path)
            return {}
        with open(path, "r", encoding="utf-8") as fh:
            data = self._yaml.load(fh)
        return dict(data) if data else {}

    def _parse_pipeline_yaml(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Flatten the nested YAML structure into PipelineConfiguration fields."""
        flat: dict[str, Any] = {}

        flat["openrouter_api_key"] = raw.get("openrouter_api_key", "")

        models = raw.get("models", {})
        flat["default_model"] = models.get("default", "")
        per_step = models.get("per_step", {})
        flat["step_models"] = {int(k): v for k, v in per_step.items()} if per_step else {}

        retry = raw.get("retry", {})
        if "test_failure_limit" in retry:
            flat["test_retry_limit"] = retry["test_failure_limit"]
        if "infrastructure_limit" in retry:
            flat["infra_retry_limit"] = retry["infrastructure_limit"]
        if "infrastructure_backoff" in retry:
            flat["infra_retry_backoff"] = retry["infrastructure_backoff"]

        sim = raw.get("simulator", {})
        if "endpoint" in sim:
            flat["simulator_endpoint"] = sim["endpoint"]
        if "port" in sim:
            flat["simulator_port"] = sim["port"]

        ext = raw.get("external_modules", {})
        if "ciq_processor_jar" in ext:
            flat["ciq_processor_jar"] = ext["ciq_processor_jar"]
        if "cli_automation_jar" in ext:
            flat["cli_automation_jar"] = ext["cli_automation_jar"]

        storage = raw.get("storage", {})
        if "artifacts_directory" in storage:
            flat["output_directory"] = storage["artifacts_directory"]
        if "database_path" in storage:
            flat["database_path"] = storage["database_path"]
        if "knowledge_base_directory" in storage:
            flat["knowledge_base_directory"] = storage["knowledge_base_directory"]

        chunking = raw.get("chunking", {})
        if "ciq_rows_per_chunk" in chunking:
            flat["chunk_size_rows"] = chunking["ciq_rows_per_chunk"]

        few_shot = raw.get("few_shot", {})
        if "top_k" in few_shot:
            flat["few_shot_count"] = few_shot["top_k"]

        return flat

    def _merge_models_yaml(self, flat: dict[str, Any], models_raw: dict[str, Any]) -> None:
        """Merge per-step model overrides from models.yaml into the flat config."""
        steps = models_raw.get("steps", {})
        if not steps:
            return
        step_models = flat.get("step_models", {})
        for step_id, step_info in steps.items():
            sid = int(step_id)
            model_val = step_info.get("model") if isinstance(step_info, dict) else None
            # Only override if models.yaml specifies a non-null model
            if model_val is not None:
                step_models[sid] = model_val
            elif sid not in step_models:
                step_models[sid] = None
        flat["step_models"] = step_models

    async def _load_db_overrides(self) -> dict[str, Any]:
        """Fetch config overrides stored in the database, if a DB accessor is available."""
        if self._db_accessor is None:
            return {}
        try:
            if hasattr(self._db_accessor, "get_config_overrides"):
                overrides = await self._db_accessor.get_config_overrides()
                if isinstance(overrides, dict):
                    return overrides
        except Exception:
            logger.exception("Failed to load DB config overrides")
        return {}

    async def _build_config(self) -> PipelineConfiguration:
        """Build the full PipelineConfiguration from all sources."""
        # 1. Load base YAML
        raw = self._load_yaml(self._config_path)
        flat = self._parse_pipeline_yaml(raw)

        # 2. Merge models.yaml
        models_raw = self._load_yaml(self._models_path)
        self._merge_models_yaml(flat, models_raw)

        # 3. Apply DB overrides (highest priority)
        db_overrides = await self._load_db_overrides()
        if db_overrides:
            flat.update(db_overrides)

        config = PipelineConfiguration(**flat)
        logger.info("Configuration loaded: %s", config.masked_dict())
        return config

    # -- public interface ----------------------------------------------------

    async def get_config(self) -> PipelineConfiguration:
        """Return the current pipeline configuration.

        Loads from files + DB on first call; returns cached copy thereafter.
        """
        if self._config is None:
            self._config = await self._build_config()
        return self._config

    async def update_config(self, updates: dict[str, Any]) -> PipelineConfiguration:
        """Apply *updates* on top of the current configuration and return the new state.

        The updated config is re-validated through Pydantic. API keys in
        *updates* are never logged.
        """
        current = await self.get_config()
        merged = current.model_dump()
        merged.update(updates)

        # Log safely — mask any key that looks like an API key
        safe_updates = {
            k: mask_api_key(v) if "api_key" in k and isinstance(v, str) else v
            for k, v in updates.items()
        }
        logger.info("Updating configuration: %s", safe_updates)

        self._config = PipelineConfiguration(**merged)

        # Persist to DB if accessor supports it
        if self._db_accessor is not None and hasattr(self._db_accessor, "save_config_overrides"):
            try:
                await self._db_accessor.save_config_overrides(updates)
            except Exception:
                logger.exception("Failed to persist config overrides to DB")

        return self._config

    async def validate_api_key(self, key: str) -> bool:
        """Basic format check for an OpenRouter API key.

        Checks that the key is non-empty and matches expected patterns
        (starts with ``sk-`` and has reasonable length).  This does **not**
        make a network call.
        """
        if not key or not isinstance(key, str):
            return False
        key = key.strip()
        if len(key) < 8:
            return False
        if key.startswith("sk-"):
            return True
        # Some providers use different prefixes — accept if length is plausible
        return len(key) >= 20

    async def list_available_models(self) -> list[ModelInfo]:
        """Return the list of models defined in ``config/models.yaml`` plus well-known defaults.

        Each entry includes metadata useful for the UI model-selector.
        """
        # Well-known model catalogue (can be extended or loaded from a remote registry)
        catalogue: dict[str, ModelInfo] = {
            "openrouter/anthropic/claude-sonnet-4-20250514": ModelInfo(
                model_id="openrouter/anthropic/claude-sonnet-4-20250514",
                display_name="Claude Sonnet 4 (2025-05-14)",
                context_window=200_000,
                cost_per_1k_tokens=0.003,
            ),
            "openrouter/anthropic/claude-opus-4-20250514": ModelInfo(
                model_id="openrouter/anthropic/claude-opus-4-20250514",
                display_name="Claude Opus 4 (2025-05-14)",
                context_window=200_000,
                cost_per_1k_tokens=0.015,
            ),
            "openrouter/google/gemini-2.5-pro": ModelInfo(
                model_id="openrouter/google/gemini-2.5-pro",
                display_name="Gemini 2.5 Pro",
                context_window=1_000_000,
                cost_per_1k_tokens=0.00125,
            ),
            "openrouter/openai/gpt-4o": ModelInfo(
                model_id="openrouter/openai/gpt-4o",
                display_name="GPT-4o",
                context_window=128_000,
                cost_per_1k_tokens=0.005,
            ),
            "openrouter/openai/gpt-4.1": ModelInfo(
                model_id="openrouter/openai/gpt-4.1",
                display_name="GPT-4.1",
                context_window=1_000_000,
                cost_per_1k_tokens=0.002,
            ),
        }

        # Collect any model IDs referenced in models.yaml that aren't in the catalogue
        models_raw = self._load_yaml(self._models_path)
        steps = models_raw.get("steps", {})
        for step_info in steps.values():
            if isinstance(step_info, dict):
                model_id = step_info.get("model")
                if model_id and model_id not in catalogue:
                    catalogue[model_id] = ModelInfo(
                        model_id=model_id,
                        display_name=model_id,
                        context_window=0,
                        cost_per_1k_tokens=0.0,
                    )

        return list(catalogue.values())

    def reload(self) -> None:
        """Invalidate the cached config so the next ``get_config()`` re-reads from disk/DB."""
        self._config = None
