# Data Model: Agentic UC Pipeline

**Date**: 2026-10-06 | **Plan**: [plan.md](plan.md)

## Entity Relationship Overview

```
UCDocumentSet 1──* PipelineRun 1──* StepResult
                                  1──* RetryAttempt
                                  1──* HILRequest
                                  1──1 TestReport
PipelineRun *──1 PipelineConfiguration
PipelineRun 1──* GeneratedConfig
KnowledgeBaseEntry 1──* GeneratedConfig (reference)
```

## Entities

### UCDocumentSet

Represents the collection of uploaded files for a single use case.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK, auto-generated | Unique identifier |
| uc_name | string | required, max 200 chars | User-provided name for the use case |
| ciq_file_path | string | required | Path to uploaded CIQ Excel file |
| mop_file_path | string | required | Path to uploaded MOP document |
| constraints_file_path | string | nullable | Path to constraints file (optional for some UCs) |
| command_outputs_path | string | required | Path to command output files |
| uploaded_at | datetime | auto-set | Upload timestamp |
| validated | boolean | default false | Whether pre-upload validation passed |
| validation_errors | JSON | nullable | List of validation errors if any |

### PipelineRun

A single execution of the 9-step pipeline for one UC document set.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK, auto-generated | Unique identifier |
| document_set_id | UUID | FK → UCDocumentSet | Associated document set |
| status | enum | required | pending, running, paused_hil, completed, failed, cancelled |
| current_step | integer | 0-9 | Currently executing step number |
| started_at | datetime | nullable | Pipeline start timestamp |
| completed_at | datetime | nullable | Pipeline completion timestamp |
| duration_seconds | float | nullable | Total elapsed time |
| config_snapshot | JSON | required | Snapshot of PipelineConfiguration at run start |
| intermediate_repr | JSON | nullable | Unified intermediate representation from step 1-2 |
| error_summary | string | nullable | Top-level error if pipeline failed |
| is_regression_run | boolean | default false | Whether this UC had a previous successful run |

**State transitions:**
```
pending → running → completed
                  → failed
                  → cancelled
running → paused_hil → running (after HIL response)
```

### StepResult

Result of a single pipeline step execution.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK, auto-generated | Unique identifier |
| run_id | UUID | FK → PipelineRun | Associated pipeline run |
| step_number | integer | 1-9 | Pipeline step number |
| step_name | string | required | Human-readable step name |
| status | enum | required | pending, in_progress, passed, failed, retrying, skipped |
| started_at | datetime | nullable | Step start timestamp |
| completed_at | datetime | nullable | Step completion timestamp |
| input_summary | JSON | nullable | Summary of input consumed |
| output_summary | JSON | nullable | Summary of output produced |
| output_artifact_paths | JSON | nullable | List of file paths to generated artifacts |
| error_message | string | nullable | Error details if failed |
| retry_count | integer | default 0 | Number of retries attempted |
| agent_model | string | nullable | LLM model used for this step |

### RetryAttempt

Record of a single retry attempt for a failed step.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK, auto-generated | Unique identifier |
| step_result_id | UUID | FK → StepResult | Associated step |
| attempt_number | integer | required, >= 1 | Retry attempt sequence |
| retry_type | enum | required | test_failure, infrastructure_error |
| error_context | JSON | required | Error message, failing test case, expected vs actual |
| agent_prompt | text | nullable | Full prompt sent to agent (for audit) |
| agent_response | text | nullable | Full response from agent (for audit) |
| outcome | enum | required | success, failure |
| started_at | datetime | required | Attempt start timestamp |
| completed_at | datetime | required | Attempt completion timestamp |

### HILRequest

A Human-in-the-Loop pause event.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK, auto-generated | Unique identifier |
| run_id | UUID | FK → PipelineRun | Associated pipeline run |
| step_number | integer | required | Step that triggered the HIL |
| field_or_section | string | required | Specific field/section needing clarification |
| agent_inference | string | required | What the agent already inferred |
| question | string | required | Specific question for the user |
| options | JSON | nullable | Suggested answer options if applicable |
| user_response | text | nullable | User's answer |
| status | enum | required | pending, answered, expired |
| created_at | datetime | auto-set | When HIL was raised |
| answered_at | datetime | nullable | When user responded |

### GeneratedConfig

A YAML configuration file or Python script produced by an agent step.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK, auto-generated | Unique identifier |
| run_id | UUID | FK → PipelineRun | Associated pipeline run |
| config_type | enum | required | validation_rules, json_template, workflow, python_script, simulator_config |
| file_path | string | required | Path to generated file |
| schema_valid | boolean | nullable | Whether schema validation passed |
| version | integer | default 1 | Incremented on retry regeneration |
| generated_by_step | integer | required | Step number that produced this config |
| content_hash | string | required | SHA256 hash for regression diff |

### TestReport

Report generated for a pipeline run.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK, auto-generated | Unique identifier |
| run_id | UUID | FK → PipelineRun, unique | One report per run |
| html_report_path | string | required | Path to HTML report file |
| excel_report_path | string | required | Path to Excel report file |
| total_tests | integer | required | Total test count |
| passed_tests | integer | required | Passed test count |
| failed_tests | integer | required | Failed test count |
| has_regression_diff | boolean | default false | Whether regression diff was included |
| generated_at | datetime | auto-set | Report generation timestamp |

### PipelineConfiguration

Settings governing pipeline behavior.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK, auto-generated | Unique identifier |
| openrouter_api_key | string | encrypted | OpenRouter API key (masked in UI) |
| default_model | string | required | Default LLM model for all steps |
| step_models | JSON | nullable | Per-step model overrides: `{step_number: model_id}` |
| test_retry_limit | integer | default 3, min 1, max 10 | Max test-failure retries |
| infra_retry_limit | integer | default 3, min 1, max 10 | Max infrastructure retries |
| infra_retry_backoff | float | default 2.0 | Exponential backoff multiplier |
| simulator_endpoint | string | required | Mock simulator URL/path |
| ciq_processor_jar | string | required | Path to ciq-processor JAR |
| cli_automation_jar | string | required | Path to cli-automation-standalone JAR |
| output_directory | string | required | Base directory for pipeline run artifacts |
| chunk_size_rows | integer | default 100 | CIQ Excel rows per chunk |
| few_shot_count | integer | default 3 | Number of few-shot examples to include |
| updated_at | datetime | auto-set | Last modification timestamp |

### KnowledgeBaseEntry

A record in the few-shot knowledge base.

| Field | Type | Constraints | Description |
|-------|------|-------------|-------------|
| id | UUID | PK, auto-generated | Unique identifier |
| uc_name | string | required | UC name for identification |
| uc_description | string | required | UC description for similarity matching |
| embedding | BLOB | required | Vector embedding of UC description |
| validation_rules_path | string | required | Path to successful validation rules YAML |
| json_template_path | string | required | Path to successful JSON template YAML |
| workflow_path | string | required | Path to successful workflow YAML |
| scripts_paths | JSON | nullable | Paths to any generated Python scripts |
| source_run_id | UUID | FK → PipelineRun | Pipeline run that produced these configs |
| created_at | datetime | auto-set | Entry creation timestamp |

## Intermediate Representation Schema

The unified structure produced by steps 1-2, consumed by all downstream agents.

```yaml
uc_metadata:
  name: string
  description: string
  uc_type: string          # e.g., "VoLTE_provisioning"
  source_documents:
    ciq_file: string
    mop_file: string
    constraints_file: string | null
    command_outputs_file: string

ciq_data:
  headers: list[string]
  rows: list[dict]         # Each row as {header: value}
  field_types:             # Inferred or HIL-confirmed
    field_name:
      type: string         # string, numeric, email, ip_address, etc.
      validation: string   # auto-inferred or HIL-provided
      hil_confirmed: boolean

mop_data:
  mop_type: enum           # cli, rest, mixed
  steps:
    - step_number: integer
      description: string
      command_type: enum   # cli_command, rest_api_call
      command_template: string  # With {variable} placeholders
      expected_output_pattern: string
      variables:           # Mapped from CIQ fields
        - name: string
          source_field: string  # CIQ header name
      pre_checks: list[string]
      post_checks: list[string]

constraints:
  field_constraints:       # Structured, auto-extracted
    - field: string
      rule_type: enum      # required, regex, range, enum, cross_field
      parameters: dict
  business_rules:          # Free-form, may need HIL
    - description: string
      extracted_rule: string | null
      hil_required: boolean

command_outputs:
  commands:
    - command: string
      expected_response: string
      response_pattern: string  # Regex or template for matching
      variables_in_response: list[string]

simulator_config:          # Auto-generated in step 6
  mock_entries:
    - command_pattern: string
      response_template: string
      variables: dict
```
