"""Core Pydantic models for all pipeline entities per data-model.md."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def _uuid() -> uuid.UUID:
    return uuid.uuid4()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------- Enums ----------

class PipelineStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PAUSED_HIL = "paused_hil"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    PASSED = "passed"
    FAILED = "failed"
    RETRYING = "retrying"
    SKIPPED = "skipped"


class RetryType(str, Enum):
    TEST_FAILURE = "test_failure"
    INFRASTRUCTURE_ERROR = "infrastructure_error"


class RetryOutcome(str, Enum):
    SUCCESS = "success"
    FAILURE = "failure"


class HILStatus(str, Enum):
    PENDING = "pending"
    ANSWERED = "answered"
    EXPIRED = "expired"


class ConfigType(str, Enum):
    VALIDATION_RULES = "validation_rules"
    JSON_TEMPLATE = "json_template"
    WORKFLOW = "workflow"
    PYTHON_SCRIPT = "python_script"
    SIMULATOR_CONFIG = "simulator_config"


# ---------- Entities ----------

class UCDocumentSet(BaseModel):
    id: uuid.UUID = Field(default_factory=_uuid)
    uc_name: str = Field(..., max_length=200)
    ciq_file_path: str
    mop_file_path: str
    constraints_file_path: str | None = None
    command_outputs_path: str
    uploaded_at: datetime = Field(default_factory=_now)
    validated: bool = False
    validation_errors: list[dict[str, Any]] | None = None
    selected_sheets: list[str] | None = None  # None = all sheets


class PipelineRun(BaseModel):
    id: uuid.UUID = Field(default_factory=_uuid)
    document_set_id: uuid.UUID
    status: PipelineStatus = PipelineStatus.PENDING
    current_step: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: float | None = None
    config_snapshot: dict[str, Any] = Field(default_factory=dict)
    intermediate_repr: dict[str, Any] | None = None
    error_summary: str | None = None
    is_regression_run: bool = False


class StepResult(BaseModel):
    id: uuid.UUID = Field(default_factory=_uuid)
    run_id: uuid.UUID
    step_number: int = Field(..., ge=1, le=9)
    step_name: str
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    input_summary: dict[str, Any] | None = None
    output_summary: dict[str, Any] | None = None
    output_artifact_paths: list[str] | None = None
    error_message: str | None = None
    retry_count: int = 0
    agent_model: str | None = None


class RetryAttempt(BaseModel):
    id: uuid.UUID = Field(default_factory=_uuid)
    step_result_id: uuid.UUID
    attempt_number: int = Field(..., ge=1)
    retry_type: RetryType
    error_context: dict[str, Any]
    agent_prompt: str | None = None
    agent_response: str | None = None
    outcome: RetryOutcome
    started_at: datetime = Field(default_factory=_now)
    completed_at: datetime = Field(default_factory=_now)


class HILRequest(BaseModel):
    id: uuid.UUID = Field(default_factory=_uuid)
    run_id: uuid.UUID
    step_number: int
    field_or_section: str
    agent_inference: str
    question: str
    options: list[str] | None = None
    user_response: str | None = None
    status: HILStatus = HILStatus.PENDING
    created_at: datetime = Field(default_factory=_now)
    answered_at: datetime | None = None


class GeneratedConfig(BaseModel):
    id: uuid.UUID = Field(default_factory=_uuid)
    run_id: uuid.UUID
    config_type: ConfigType
    file_path: str
    schema_valid: bool | None = None
    version: int = 1
    generated_by_step: int
    content_hash: str = ""


class TestReport(BaseModel):
    id: uuid.UUID = Field(default_factory=_uuid)
    run_id: uuid.UUID
    html_report_path: str
    excel_report_path: str
    total_tests: int = 0
    passed_tests: int = 0
    failed_tests: int = 0
    has_regression_diff: bool = False
    generated_at: datetime = Field(default_factory=_now)


class PipelineConfiguration(BaseModel):
    id: uuid.UUID = Field(default_factory=_uuid)
    openrouter_api_key: str = ""
    default_model: str = "openrouter/anthropic/claude-sonnet-4-20250514"
    step_models: dict[int, str | None] = Field(default_factory=dict)
    test_retry_limit: int = Field(default=3, ge=1, le=10)
    infra_retry_limit: int = Field(default=3, ge=1, le=10)
    infra_retry_backoff: float = 2.0
    simulator_endpoint: str = "localhost"
    simulator_port: int = 8080
    ciq_processor_jar: str = "./lib/ciq-processor.jar"
    cli_automation_jar: str = "./lib/cli-automation-standalone.jar"
    output_directory: str = "./data/runs"
    database_path: str = "./data/pipeline.db"
    knowledge_base_directory: str = "./data/knowledge"
    chunk_size_rows: int = 100
    few_shot_count: int = 3
    updated_at: datetime = Field(default_factory=_now)


class KnowledgeBaseEntry(BaseModel):
    id: uuid.UUID = Field(default_factory=_uuid)
    uc_name: str
    uc_description: str = ""
    embedding: list[float] = Field(default_factory=list)
    validation_rules_path: str
    json_template_path: str
    workflow_path: str
    scripts_paths: list[str] | None = None
    source_run_id: uuid.UUID
    created_at: datetime = Field(default_factory=_now)


# ---------- Contract support types ----------

class ValidationError(BaseModel):
    document: str
    error_type: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ValidationResult(BaseModel):
    valid: bool
    errors: list[ValidationError] = Field(default_factory=list)


class TestFailure(BaseModel):
    test_name: str
    expected: str = ""
    actual: str = ""
    error_message: str = ""
    context: dict[str, Any] = Field(default_factory=dict)


class TestResult(BaseModel):
    passed: bool
    total_assertions: int = 0
    passed_assertions: int = 0
    failed_assertions: int = 0
    failures: list[TestFailure] = Field(default_factory=list)
    execution_time_seconds: float = 0.0


class StepOutput(BaseModel):
    status: str  # "passed", "failed", "needs_hil"
    artifacts: list[str] = Field(default_factory=list)
    output_data: dict[str, Any] = Field(default_factory=dict)
    hil_requests: list[HILRequest] | None = None
    error: dict[str, Any] | None = None


class RetryContext(BaseModel):
    attempt_number: int
    previous_error: dict[str, Any]
    previous_output: dict[str, Any] = Field(default_factory=dict)


class ModelInfo(BaseModel):
    model_id: str
    display_name: str
    context_window: int = 0
    cost_per_1k_tokens: float = 0.0
