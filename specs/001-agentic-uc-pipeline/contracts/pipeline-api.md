# Pipeline API Contract

**Date**: 2026-10-06 | **Plan**: [../plan.md](../plan.md)

## Pipeline Orchestrator Interface

### `PipelineOrchestrator`

The central controller that manages pipeline execution.

```python
class PipelineOrchestrator:
    async def start_run(document_set_id: UUID, config: PipelineConfiguration) -> PipelineRun
    async def cancel_run(run_id: UUID) -> PipelineRun
    async def get_run_status(run_id: UUID) -> PipelineRun
    async def respond_to_hil(hil_id: UUID, response: str) -> None
    async def list_runs(filters: RunFilters) -> list[PipelineRun]
    async def delete_run_artifacts(run_id: UUID, artifact_ids: list[UUID]) -> None
```

### `RunFilters`

```python
class RunFilters:
    uc_name: str | None        # Partial match
    status: str | None         # exact match: completed, failed, cancelled
    date_from: datetime | None
    date_to: datetime | None
    page: int = 1
    page_size: int = 20
```

## Agent Step Interface

Every pipeline agent step implements this contract.

```python
class AgentStep(Protocol):
    step_number: int
    step_name: str

    async def execute(
        self,
        input_data: dict,           # Intermediate representation or prior step output
        config: PipelineConfiguration,
        few_shot_examples: list[dict] | None,
        retry_context: RetryContext | None,
    ) -> StepOutput

class StepOutput:
    status: Literal["passed", "failed", "needs_hil"]
    artifacts: list[str]         # File paths to generated artifacts
    output_data: dict            # Data passed to next step
    hil_requests: list[HILRequest] | None
    error: StepError | None

class StepError:
    message: str
    details: dict                # Failing test case, expected vs actual, etc.
    retryable: bool              # Whether test-failure retry should engage

class RetryContext:
    attempt_number: int
    previous_error: StepError
    previous_output: dict        # What was generated last time
```

## Pipeline Event Stream

Real-time events emitted by the pipeline for UI consumption.

```python
class PipelineEvent:
    run_id: UUID
    event_type: Literal[
        "step_started",
        "step_completed",
        "step_failed",
        "retry_started",
        "hil_requested",
        "hil_responded",
        "run_completed",
        "run_failed",
        "run_cancelled",
    ]
    step_number: int | None
    timestamp: datetime
    payload: dict               # Event-specific data
```

Events are published via an async event bus. The NiceGUI UI subscribes to events for a specific `run_id` and updates the UI via WebSocket binding.

## Test Harness Interface

```python
class TestHarness:
    async def run_validation_tests(
        ciq_data_path: str,
        validation_rules_path: str,
        ciq_processor_jar: str,
    ) -> TestResult

    async def run_workflow_tests(
        workflow_path: str,
        simulator_config_path: str,
        cli_automation_jar: str,
    ) -> TestResult

    async def run_schema_validation(
        config_path: str,
        schema_path: str,
    ) -> TestResult

class TestResult:
    passed: bool
    total_assertions: int
    passed_assertions: int
    failed_assertions: int
    failures: list[TestFailure]
    execution_time_seconds: float

class TestFailure:
    test_name: str
    expected: str
    actual: str
    error_message: str
    context: dict               # CIQ record, command, etc.
```

## Few-Shot Knowledge Base Interface

```python
class KnowledgeBase:
    async def add_entry(
        uc_name: str,
        uc_description: str,
        configs: dict[str, str],  # config_type → file_path
        source_run_id: UUID,
    ) -> KnowledgeBaseEntry

    async def find_similar(
        uc_description: str,
        top_k: int = 3,
    ) -> list[KnowledgeBaseEntry]

    async def get_few_shot_context(
        uc_description: str,
        top_k: int = 3,
    ) -> list[dict]              # Ready-to-use few-shot examples for prompts
```

## Document Upload & Validation Interface

```python
class DocumentUploader:
    async def upload_documents(
        uc_name: str,
        ciq_file: UploadedFile,
        mop_file: UploadedFile,
        constraints_file: UploadedFile | None,
        command_outputs_file: UploadedFile,
    ) -> UCDocumentSet

    async def validate_documents(
        document_set_id: UUID,
    ) -> ValidationResult

class ValidationResult:
    valid: bool
    errors: list[ValidationError]  # Missing columns, wrong format, etc.

class ValidationError:
    document: str               # "ciq", "mop", "constraints", "command_outputs"
    error_type: str             # "missing_column", "invalid_format", "empty_content"
    message: str
    details: dict
```

## Configuration Management Interface

```python
class ConfigManager:
    async def get_config() -> PipelineConfiguration
    async def update_config(updates: dict) -> PipelineConfiguration
    async def validate_api_key(key: str) -> bool
    async def list_available_models() -> list[ModelInfo]

class ModelInfo:
    model_id: str               # e.g., "openrouter/anthropic/claude-sonnet-4-20250514"
    display_name: str
    context_window: int
    cost_per_1k_tokens: float
```
