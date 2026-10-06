# Tasks: Agentic UC Pipeline

**Input**: Design documents from `specs/001-agentic-uc-pipeline/`

**Prerequisites**: plan.md (required), spec.md (required for user stories), research.md, data-model.md, contracts/

**Tests**: Tests are not explicitly requested in the spec. Test tasks are omitted.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing of each story.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (e.g., US1, US2, US3)
- Include exact file paths in descriptions

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project initialization, dependency installation, and basic directory structure

- [x] T001 Create project directory structure per plan.md: `src/agents/`, `src/core/`, `src/parsers/`, `src/testing/`, `src/testing/report/`, `src/testing/report/templates/`, `src/knowledge/`, `src/ui/`, `src/ui/pages/`, `src/storage/`, `src/prompts/`, `schemas/`, `config/`, `tests/unit/`, `tests/integration/`, `tests/fixtures/`, `data/runs/`, `data/knowledge/`, `data/logs/`
- [x] T002 Create `pyproject.toml` with dependencies: google-adk, litellm, nicegui, openpyxl, python-docx, PyPDF2, ruamel.yaml, jinja2, xlsxwriter, sentence-transformers, pydantic, aiosqlite
- [x] T003 [P] Create `config/pipeline_config.yaml` with default settings per contracts/config-schemas.md: OpenRouter API key placeholder, default model, retry limits (test: 3, infra: 3), backoff multiplier (2.0), simulator endpoint, JAR paths, storage paths, chunking defaults (100 rows), few-shot (top_k: 3), UI settings (host: 0.0.0.0, port: 8000, dark_mode: true)
- [x] T004 [P] Create `config/models.yaml` with per-step model assignments (steps 1-6 default to null, steps 7-9 marked as deterministic/no LLM)
- [x] T005 [P] Create YAML schema files from contracts/config-schemas.md: `schemas/validation_rules.schema.yaml`, `schemas/json_template.schema.yaml`, `schemas/workflow.schema.yaml`, `schemas/simulator_config.schema.yaml`
- [x] T006 [P] Create sample test fixtures: `tests/fixtures/sample_ciq.xlsx` (5+ rows, multiple sheets, header-name columns), `tests/fixtures/sample_mop.docx` (CLI + REST steps), `tests/fixtures/sample_constraints.txt` (mixed structured tables + free-form), `tests/fixtures/sample_command_outputs.txt` (CLI session with commands and responses)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Core infrastructure that MUST be complete before ANY user story can be implemented

**CRITICAL**: No user story work can begin until this phase is complete

- [x] T007 Implement Pydantic models for all entities in `src/core/models.py`: UCDocumentSet (id: UUID, uc_name: str max 200, ciq_file_path: str required, mop_file_path: str required, constraints_file_path: str nullable, command_outputs_path: str required, uploaded_at: datetime, validated: bool default false, validation_errors: JSON nullable), PipelineRun (id: UUID, document_set_id: UUID FK, status: enum[pending,running,paused_hil,completed,failed,cancelled], current_step: int 0-9, started_at/completed_at: datetime nullable, config_snapshot: JSON, intermediate_repr: JSON nullable, error_summary: str nullable, is_regression_run: bool default false), StepResult, RetryAttempt, HILRequest, GeneratedConfig, TestReport, PipelineConfiguration, KnowledgeBaseEntry per data-model.md
- [x] T008 Implement SQLite database layer in `src/storage/db.py`: create tables for pipeline_runs, step_results, retry_attempts, hil_requests, generated_configs, test_reports, settings, knowledge_base_entries; include `--init` CLI flag for database initialization; use aiosqlite for async operations
- [x] T009 Implement artifact file store in `src/storage/artifact_store.py`: create/read/delete artifacts under `data/runs/<run_id>/` with subdirectories per step; support cleanup of selected artifacts (FR-022); compute SHA256 content hashes for regression diff (FR-040)
- [x] T010 [P] Implement configuration manager in `src/core/config.py`: load from `config/pipeline_config.yaml`, override with DB-stored settings, support per-step model assignments, API key encryption/masking (FR-042 — display as `sk-...xxxx`, never log keys), expose get/update/validate_api_key/list_available_models per contracts/pipeline-api.md ConfigManager interface
- [x] T011 [P] Implement intermediate representation schema in `src/core/intermediate_repr.py`: Pydantic model matching data-model.md Intermediate Representation Schema (uc_metadata, ciq_data with field_types and hil_confirmed flags, mop_data with step sequences and command_type enum[cli_command, rest_api_call], constraints with field_constraints and business_rules, command_outputs with response patterns, simulator_config with mock_entries)
- [x] T012 [P] Implement pipeline event bus in `src/core/events.py`: async event emitter for PipelineEvent (run_id: UUID, event_type: Literal[step_started, step_completed, step_failed, retry_started, hil_requested, hil_responded, run_completed, run_failed, run_cancelled], step_number: int|None, timestamp: datetime, payload: dict) per contracts/pipeline-api.md; support subscribe/unsubscribe per run_id for UI consumption via WebSocket

- [x] T057 Implement NiceGUI app entry point in `src/ui/app.py`: single-page layout with `ui.header(fixed=True)` for sticky progress banner; collapsible `ui.expansion()` sections in order: Upload, Pipeline Progress, Run History, Cleanup, Settings (FR-011); initialize DB on startup; load config; bind event bus to WebSocket for real-time updates

**Checkpoint**: Foundation ready - user story implementation can now begin

---

## Phase 3: User Story 3 - Document Upload and Validation (Priority: P1)

**Goal**: Telecom engineer uploads UC documents through the UI, system validates format and content before pipeline can start

**Independent Test**: Upload valid and invalid document sets; verify acceptance/rejection with clear error messages; pipeline start button enables only on valid uploads

### Implementation for User Story 3

- [x] T013 [P] [US3] Implement CIQ Excel parser in `src/parsers/ciq_parser.py`: header-name-based column identification (FR-026), tolerant of reordering and extra columns, process all sheets by default (FR-043), row-based chunking at configurable threshold (default 100 rows from config), validate mandatory columns exist, return parsed headers + rows as list[dict]; support sheet/column exclusion if specified by user or constraints
- [x] T014 [P] [US3] Implement MOP document parser in `src/parsers/mop_parser.py`: extract CLI command templates, REST API call definitions, execution step sequences, expected outputs (FR-031); support .docx (python-docx), .pdf (PyPDF2), .txt formats; detect mop_type enum[cli, rest, mixed]; section-based chunking for large documents
- [x] T015 [P] [US3] Implement constraints parser in `src/parsers/constraints_parser.py`: extract structured tables (field constraints, value ranges, cross-field dependencies) and flag free-form business rules as needing HIL (FR-032); support mixed-format files; extract sheet/column exclusion directives if present
- [x] T016 [P] [US3] Implement command output parser in `src/parsers/command_output_parser.py`: parse CLI/API session logs, separate commands from responses, filter prompts/banners/noise, extract command-response pairs with response patterns (FR-033)
- [x] T017 [US3] Implement document upload handler in `src/ui/pages/upload_section.py`: NiceGUI `ui.upload()` components for CIQ Excel (.xlsx/.xls), MOP (.docx/.pdf/.txt), constraints (optional), command outputs (.txt); UC name input field (required, max 200 chars); sheet selection UI showing available sheets after CIQ upload (all selected by default per FR-043); file format validation on upload; call document validation on all files before enabling Start Pipeline button
- [x] T018 [US3] Implement document validation service in `src/core/document_validator.py`: validate CIQ has mandatory columns (list from config/schema), MOP is parseable and contains at least one command/step, command outputs contain at least one command-response pair; return ValidationResult with per-document errors per contracts/pipeline-api.md DocumentUploader.validate_documents interface; persist UCDocumentSet to DB on success
- [x] T019 [US3] Wire upload UI to validation: display validation errors inline per document (red highlights with specific messages like "Missing mandatory column: Hostname"), disable Start Pipeline button until all required documents pass validation (FR-002), show green checkmarks for validated documents

**Checkpoint**: Document upload and validation is fully functional and testable independently

---

## Phase 4: User Story 1 - End-to-End UC Onboarding via Pipeline (Priority: P1) — MVP

**Goal**: Complete pipeline execution from uploaded documents through 9 agent steps to generated YAML configs and test reports

**Independent Test**: Upload sample UC documents, trigger pipeline, verify 3 YAML configs generated, tests pass against CIQ data and mock simulator, HTML + Excel reports produced

### Implementation for User Story 1

- [x] T020 [US1] Implement pipeline execution engine in `src/core/pipeline.py`: PipelineOrchestrator with start_run(), cancel_run(), get_run_status(), respond_to_hil(), list_runs(), delete_run_artifacts() per contracts/pipeline-api.md; manage pipeline state transitions (pending→running→completed/failed/cancelled, running→paused_hil→running); execute steps 1-9 sequentially via dedicated agents; emit PipelineEvents on each state change; run pipeline as async background task so UI remains responsive; persist run data to DB after each step
- [x] T021 [US1] Implement HIL handler in `src/core/hil.py`: create HIL requests with field, document_section, agent_inference, question, options; publish hil_requested event; await asyncio.Event for user response (wait indefinitely per FR-030); support resume from paused step and option to restart from paused step; batch multiple HIL requests from steps 1-2; persist HIL requests to DB for browser-close recovery
- [x] T022 [US1] Implement few-shot knowledge base in `src/knowledge/few_shot_store.py`: store successful UC configs under `data/knowledge/` with UC metadata JSON + 3 YAML configs + scripts; latest-only per UC name — replace older entries (FR-044); add_entry() on successful pipeline completion per contracts/pipeline-api.md KnowledgeBase interface
- [x] T023 [P] [US1] Implement similarity matching in `src/knowledge/similarity.py`: embed UC descriptions using `all-MiniLM-L6-v2` (sentence-transformers); cosine similarity search; find_similar(uc_description, top_k=3) returning KnowledgeBaseEntry list; get_few_shot_context() formatting entries as prompt-ready examples
- [x] T024 [P] [US1] Create externalized prompt templates in `src/prompts/`: `doc_parser.yaml` (step 1), `uc_analyzer.yaml` (step 2), `validation_gen.yaml` (step 3), `json_template_gen.yaml` (step 4), `workflow_gen.yaml` (step 5), `script_gen.yaml` (step 6); each template includes system prompt, input schema description, output schema description, cr-automation schema reference, few-shot placeholder
- [x] T025 [US1] Implement ADK-liteLLM integration in `src/agents/__init__.py`: custom ModelClient that routes ADK agent LLM calls through liteLLM to OpenRouter; support per-step model configuration from PipelineConfiguration; handle model format `openrouter/<provider>/<model>`
- [x] T026 [US1] Implement Step 1 agent — Document Parser in `src/agents/doc_parser_agent.py`: Google ADK Agent with dedicated prompt from `src/prompts/doc_parser.yaml`; consume uploaded documents via parsers (T013-T016); produce unified intermediate representation (T011); use structured output via ADK output_schema; support chunked processing for large documents (FR-035) — split, process chunks, aggregate; identify fields needing HIL and raise HIL requests
- [x] T027 [US1] Implement Step 2 agent — UC Analyzer in `src/agents/uc_analyzer_agent.py`: consume intermediate representation from step 1; analyze UC requirements; enrich intermediate repr with inferred field types, validation rules for simple types (string, email, numeric auto-inferred per FR-028); identify complex/ambiguous constraints requiring HIL confirmation; batch all HIL requests and pause pipeline until all answered; output complete intermediate representation ready for generation steps
- [x] T028 [P] [US1] Implement Step 3 agent — Validation Rules Generator in `src/agents/validation_gen_agent.py`: consume intermediate representation; generate validation_rules.yaml conforming to `schemas/validation_rules.schema.yaml`; use ADK structured output; include few-shot examples from knowledge base; reference cr-automation schema docs in prompt
- [x] T029 [P] [US1] Implement Step 4 agent — JSON Template Generator in `src/agents/json_template_agent.py`: consume intermediate representation; generate json_template.yaml conforming to `schemas/json_template.schema.yaml`; map CIQ fields to JSON paths with field_mappings; use ADK structured output
- [x] T030 [P] [US1] Implement Step 5 agent — Workflow Generator in `src/agents/workflow_gen_agent.py`: consume intermediate representation (mop_data); generate workflow.yaml conforming to `schemas/workflow.schema.yaml`; support workflow_type enum[cli, rest, mixed]; map command variables to CIQ fields; include expected_output patterns from command_outputs
- [x] T031 [US1] Implement Step 6 agent — Script + Simulator Config Generator in `src/agents/script_gen_agent.py`: consume intermediate representation; optionally generate Python scripts for transformation logic not expressible in YAML (FR-006); generate simulator_config.yaml conforming to `schemas/simulator_config.schema.yaml` with mock_entries derived from command output documents (FR-021); use ADK structured output
- [x] T032 [US1] Implement Step 7 — Schema Validator (deterministic, no LLM) in `src/agents/schema_validator.py`: validate all generated YAML configs against their respective schemas using ruamel.yaml + jsonschema; validate simulator_config.yaml; return pass/fail with specific schema violation details; implements AgentStep protocol from contracts/pipeline-api.md
- [x] T033 [US1] Implement Step 8 — Test Executor (deterministic, no LLM) in `src/agents/test_executor.py`: implement TestHarness interface from contracts/pipeline-api.md; invoke ciq-processor JAR via subprocess for validation tests (`java -jar ciq-processor.jar --input <ciq> --rules <rules> --output <result.json>`); invoke cli-automation-standalone JAR for workflow tests (`java -jar cli-automation-standalone.jar --workflow <workflow> --simulator-config <sim_config> --output <result.json>`); parse JSON results; verify simulator responses match expected patterns from command output documents (FR-009); aggregate TestResult with per-assertion pass/fail
- [x] T058 [P] [US1] Create HTML report Jinja2 templates in `src/testing/report/templates/`: base template with dark theme, collapsible sections, syntax highlighting CSS; per-step section template; regression diff template; retry history template; agent prompt/response expandable template
- [x] T034 [US1] Implement Step 9 — Report Generator (deterministic, no LLM) in `src/agents/report_generator.py` (depends on T058): generate HTML report using Jinja2 templates with collapsible per-step sections, syntax-highlighted YAML, color-coded pass/fail, embedded agent prompt/response logs, actual vs. expected values, regression diff section if applicable (FR-010); generate Excel report using xlsxwriter with summary sheet (overall pass/fail, UC metadata, timestamps) and per-step sheet (step name, status, error summary) (FR-010)
- [x] T035 [US1] Implement regression diff in `src/testing/regression_diff.py`: compare new generated configs against last successful configs for the same UC (matched by UC name per FR-040); compute YAML-level diff; include diff in test report; do not block pipeline
- [x] T036 [US1] Implement basic retry logic in `src/core/retry.py`: test-failure retry — feed error context (error message, failing test case, expected vs. actual) back to the failing agent step, up to configurable retry limit (default 3) from FR-007; smart cascading — if generation step (3-6) retried, re-run dependent downstream steps (7-9) with regenerated output (FR-045); infrastructure retry — separate counter with exponential backoff for LLM API failures (FR-020); implement RetryContext from contracts/pipeline-api.md
- [x] T037 [US1] Wire pipeline orchestrator end-to-end: connect all 9 steps in sequence in `src/core/pipeline.py`; pass intermediate representation and step outputs between steps; integrate retry logic; integrate HIL handler; integrate few-shot knowledge base lookup before generation steps; persist step results and artifacts after each step; auto-add to knowledge base on successful completion; emit pipeline events throughout

**Checkpoint**: Full pipeline executes end-to-end — upload documents, generate configs, test them, produce reports. This is the MVP.

---

## Phase 5: User Story 2 - Real-Time Progress Tracking (Priority: P2)

**Goal**: UI shows real-time pipeline step status updates with sticky progress banner

**Independent Test**: Trigger pipeline run, verify UI updates step-by-step without manual refresh, verify sticky banner visible while scrolling

### Implementation for User Story 2

- [x] T038 [US2] Implement sticky progress banner in `src/ui/pages/progress_section.py`: NiceGUI `ui.header(fixed=True)` showing current pipeline run status; display current step name, step number (N/9), status (pending/in-progress/passed/failed/retrying), elapsed time; update via WebSocket subscription to PipelineEvents for the active run_id; show "retrying (attempt N of M)" with previous error summary on retry; show overall summary (pass/fail, total duration, download link) on completion
- [x] T039 [US2] Implement detailed progress section in `src/ui/pages/progress_section.py`: collapsible `ui.expansion("Pipeline Progress")` section; 9-step visual tracker with status indicators per step (color-coded: grey=pending, blue=in-progress, green=passed, red=failed, orange=retrying); per-step elapsed time; per-step error summary on failure; subscribe to PipelineEvents and update UI within 3 seconds (SC-004)
- [x] T040 [US2] Implement cancel button in progress section: "Cancel Pipeline" button visible during active runs; on click, call PipelineOrchestrator.cancel_run(); update UI to show cancelled status; preserve completed step artifacts per FR-019

**Checkpoint**: Real-time progress tracking works independently — pipeline status updates live in the UI

---

## Phase 6: User Story 4 - Retry Feedback Loop with Error Context (Priority: P2)

**Goal**: Failed tests trigger visible retry with error context fed back to agents; user observes retry attempts in UI

**Independent Test**: Provide UC that causes initial test failure, verify retry engages with error context, observe retry status in UI

### Implementation for User Story 4

- [x] T041 [US4] Enhance retry logic UI visibility in `src/ui/pages/progress_section.py`: show retry attempt count, previous error summary, and "retrying with error context" indicator per step; display full retry history in expandable section; show which error fields are being fed back to the agent
- [x] T042 [US4] Implement HIL dialog in `src/ui/pages/hil_dialog.py`: NiceGUI `ui.dialog()` presenting HIL request fields (specific field/section, agent inference, question, suggested options); inline response form — dropdown for suggested options or text input for custom answer; on submit, call PipelineOrchestrator.respond_to_hil(); support multiple batched HIL requests presented as a form with multiple questions; validate user response is non-empty
- [x] T043 [US4] Enhance pipeline failure reporting: on retry limit exhaustion (FR-016), display failure summary in UI with all attempt histories; link to full HTML report; show which step failed and how many retries were attempted

**Checkpoint**: Retry loop is visible in UI with full error context — users can observe and understand retry behavior

---

## Phase 7: User Story 5 - Artifact Download and Deployment Packaging (Priority: P3)

**Goal**: Users download all generated artifacts as a structured deployment-ready package

**Independent Test**: Complete successful pipeline run, click download, verify zip contains all expected files in correct structure

### Implementation for User Story 5

- [x] T044 [US5] Implement artifact packaging in `src/storage/artifact_store.py`: package_artifacts(run_id) creates a zip file containing validation_rules.yaml, json_template.yaml, workflow.yaml, optional Python scripts, simulator_config.yaml, report.html, report.xlsx in a structured directory layout matching cr-automation framework's UC config directory structure
- [x] T045 [US5] Implement download button in UI: add download button to progress section (visible after pipeline completion) and to run history detail view; use NiceGUI `ui.download()` to serve the zip package; include both HTML and Excel reports in the download

**Checkpoint**: Artifact download works independently — completed runs produce downloadable deployment packages

---

## Phase 8: User Story 6 - Pipeline Step Configuration (Priority: P3)

**Goal**: Settings UI section for managing all pipeline configuration (API keys, models, retry limits, endpoints)

**Independent Test**: Change retry limit in Settings, start pipeline run, verify new limit is respected

### Implementation for User Story 6

- [x] T046 [US6] Implement settings section in `src/ui/pages/settings_section.py`: NiceGUI collapsible `ui.expansion("Settings")` section; API key input with masked display (`sk-...xxxx`) and show/hide toggle (FR-042); default model dropdown populated from OpenRouter available models; per-step model assignment table (steps 1-6 with dropdowns, steps 7-9 marked deterministic); retry limit inputs (test-failure and infrastructure separate) with validation (min 1, max 10); mock simulator endpoint input; JAR path inputs for ciq-processor and cli-automation-standalone; output directory path; save button persisting to DB via ConfigManager
- [x] T047 [US6] Implement run history section in `src/ui/pages/history_section.py`: NiceGUI `ui.table()` with columns: UC name, status, started_at, duration, pass/fail; filterable by UC name (partial text match), status (dropdown), date range (date picker) per FR-024; click row to expand run details (step results, retry history, download link); pagination
- [x] T048 [US6] Implement cleanup section in `src/ui/pages/cleanup_section.py`: NiceGUI collapsible section listing pipeline runs with their artifacts; checkbox selection for individual runs or artifacts; delete button calling artifact_store.delete() and DB cleanup (FR-022); confirmation dialog before deletion

**Checkpoint**: Settings, history browsing, and cleanup all functional — complete UI experience

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: Final integration, UI polish, and cross-cutting improvements

- [x] T050 [P] Implement theme management in `src/ui/theme.py`: dark mode default via `ui.dark_mode(True)` (FR-041); light/dark toggle button in header; persist theme preference; apply Quasar dark mode classes
- [x] T051 [P] Implement landing view in `src/ui/app.py`: upload form at top (US3 upload section), summary table of recent pipeline runs below (latest 10 runs with status, UC name, duration) per FR-023
- [x] T053 Implement audit logging in `src/core/logging.py`: structured JSON logging for all pipeline executions (FR-015); log agent prompts and responses (excluding API keys per FR-042); log test results and retry attempts; log HIL requests and responses; configurable log level; write to file under `data/logs/`
- [x] T054 End-to-end integration validation: run full pipeline with sample fixtures from `tests/fixtures/`; verify all 9 steps complete; verify HTML and Excel reports generated; verify artifacts downloadable; verify run appears in history; verify settings persist; verify dark/light theme toggle; validate against quickstart.md scenarios 1-8; validate SC-001 (< 30 min), SC-002 (70% retry success)
- [x] T055 [P] Create `requirements.txt` / lock file from `pyproject.toml` for reproducible installs
- [x] T056 [P] Create `.env.example` with OPENROUTER_API_KEY placeholder and documentation comments

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies - can start immediately
- **Foundational (Phase 2)**: Depends on Setup completion - BLOCKS all user stories
- **US3 Doc Upload (Phase 3)**: Depends on Foundational — prerequisite for US1
- **US1 E2E Pipeline (Phase 4)**: Depends on Foundational + US3 (needs document upload) — **MVP**
- **US2 Progress Tracking (Phase 5)**: Depends on Foundational + US1 (needs pipeline events to display)
- **US4 Retry Loop UI (Phase 6)**: Depends on Foundational + US1 (needs retry mechanism to visualize)
- **US5 Artifact Download (Phase 7)**: Depends on US1 (needs generated artifacts)
- **US6 Configuration (Phase 8)**: Depends on Foundational (can start after Phase 2, independent of other stories)
- **Polish (Phase 9)**: Depends on all user stories being complete

### User Story Dependencies

- **US3 (P1)**: Depends on Foundational only — first story to implement
- **US1 (P1)**: Depends on US3 (needs uploaded documents) — core MVP
- **US2 (P2)**: Depends on US1 (needs running pipeline) — can be developed in parallel with stub events
- **US4 (P2)**: Depends on US1 (needs retry mechanism) — enhances US1's retry visibility
- **US5 (P3)**: Depends on US1 (needs artifacts) — lightweight addition
- **US6 (P3)**: Independent of other stories after Foundational — can be developed in parallel

### Within Each User Story

- Models/parsers before services
- Services before UI components
- Core implementation before integration
- Story complete before moving to next priority

### Parallel Opportunities

- T003, T004, T005, T006 can all run in parallel (Phase 1)
- T010, T011, T012 can run in parallel (Phase 2 — different files)
- T013, T014, T015, T016 can all run in parallel (US3 parsers — different files)
- T023, T024 can run in parallel with T025 (US1 — knowledge base + prompts independent of agents)
- T028, T029, T030 can run in parallel (US1 generation agents — steps 3, 4, 5 independent)
- US6 (Phase 8) can run in parallel with US2/US4/US5 after US1 is complete
- T058 can run in parallel with T028-T030 (US1 — report templates independent of generation agents)
- T050, T051, T055, T056 can all run in parallel (Polish phase)

---

## Parallel Example: User Story 1

```bash
# Launch all independent generation agents together:
Task T028: "Implement Step 3 agent — Validation Rules Generator"
Task T029: "Implement Step 4 agent — JSON Template Generator"
Task T030: "Implement Step 5 agent — Workflow Generator"

# Launch knowledge base components together:
Task T022: "Implement few-shot knowledge base store"
Task T023: "Implement similarity matching"

# Launch prompt templates (independent of agent code):
Task T024: "Create externalized prompt templates"
```

---

## Implementation Strategy

### MVP First (US3 + US1 Only)

1. Complete Phase 1: Setup
2. Complete Phase 2: Foundational (CRITICAL - blocks all stories)
3. Complete Phase 3: US3 - Document Upload
4. Complete Phase 4: US1 - E2E Pipeline
5. **STOP and VALIDATE**: Run full pipeline with sample fixtures
6. Deploy/demo if ready — pipeline generates configs and reports

### Incremental Delivery

1. Setup + Foundational → Foundation ready
2. US3 (Doc Upload) → Documents validated and uploaded
3. US1 (E2E Pipeline) → **MVP!** Full pipeline works end-to-end
4. US2 (Progress Tracking) → Real-time visibility added
5. US4 (Retry Loop UI) → Retry behavior visible to users
6. US5 (Artifact Download) → Deployment packaging added
7. US6 (Configuration) → Settings UI complete
8. Polish → Production-ready

### Parallel Team Strategy

With multiple developers after Foundational is complete:

1. Team completes Setup + Foundational together
2. Once Foundational is done:
   - Developer A: US3 → US1 (critical path — parsers then pipeline)
   - Developer B: US6 (Settings — independent after Foundational)
3. After US1 is complete:
   - Developer A: US2 (Progress Tracking)
   - Developer B: US4 (Retry Loop UI)
   - Developer C: US5 (Artifact Download)
4. Polish phase: all developers

---

## Notes

- [P] tasks = different files, no dependencies on incomplete tasks
- [Story] label maps task to specific user story for traceability
- Each user story is independently completable and testable after its prerequisites
- Steps 7-9 are deterministic (no LLM) — they invoke Java JARs or generate reports
- JAR paths for ciq-processor and cli-automation-standalone must be configured before US1 testing
- JRE 11+ must be available in the runtime environment
- Commit after each task or logical group
- Stop at any checkpoint to validate story independently
