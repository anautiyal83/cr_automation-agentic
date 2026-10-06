# Quickstart Validation Guide: Agentic UC Pipeline

**Date**: 2026-10-06 | **Plan**: [plan.md](plan.md)

## Prerequisites

- Python 3.11+
- Java Runtime Environment (JRE 11+) — required for ciq-processor and cli-automation-standalone
- OpenRouter API key with active credits
- cr-automation JARs:
  - `ciq-processor.jar` placed in `./lib/`
  - `cli-automation-standalone.jar` placed in `./lib/`

## Setup

```bash
# Clone and install
git clone <repo-url> && cd cr_automation-agentic
python -m venv .venv && source .venv/bin/activate  # or .venv\Scripts\activate on Windows
pip install -r requirements.txt

# Verify Java availability
java -version

# Verify JAR files
java -jar lib/ciq-processor.jar --help
java -jar lib/cli-automation-standalone.jar --help

# Configure API key (or set via UI Settings later)
export OPENROUTER_API_KEY="sk-or-..."

# Initialize database
python -m src.storage.db --init

# Start the application
python -m src.ui.app
# Opens at http://localhost:8000
```

## Validation Scenarios

### Scenario 1: Successful End-to-End Pipeline Run

**Purpose**: Validate the complete pipeline from document upload through artifact generation and test reporting.

**Steps**:
1. Open `http://localhost:8000` in a browser.
2. In the **Settings** section, verify OpenRouter API key is configured and a default model is selected.
3. In the **Upload** section:
   - Enter UC name: "Test_VoLTE_Basic"
   - Upload `tests/fixtures/sample_ciq.xlsx`
   - Upload `tests/fixtures/sample_mop.docx`
   - Upload `tests/fixtures/sample_constraints.txt`
   - Upload `tests/fixtures/sample_command_outputs.txt`
4. Click **Start Pipeline**.
5. Observe the sticky progress banner updating step-by-step.
6. Wait for all 9 steps to complete.

**Expected outcomes**:
- All 9 steps show "passed" status with green indicators.
- Generated artifacts appear in `data/runs/<run_id>/`:
  - `validation_rules.yaml` — conforms to [validation rules schema](contracts/config-schemas.md#validation-rules-schema)
  - `json_template.yaml` — conforms to [JSON template schema](contracts/config-schemas.md#json-template-schema)
  - `workflow.yaml` — conforms to [workflow schema](contracts/config-schemas.md#workflow-schema)
  - `simulator_config.yaml` — conforms to [simulator config schema](contracts/config-schemas.md#simulator-config-schema)
  - `report.html` — full-detail test report
  - `report.xlsx` — summary test report
- Download button produces a zip with all artifacts.
- Pipeline run appears in the **History** section with "completed" status.
- Knowledge base entry auto-created for this UC.

### Scenario 2: Document Upload Validation Failure

**Purpose**: Validate that invalid documents are rejected before pipeline execution.

**Steps**:
1. Upload a CIQ Excel file with missing mandatory columns (e.g., remove "Hostname" column from sample).
2. Upload remaining documents normally.
3. Observe validation results.

**Expected outcomes**:
- Upload validation shows error: "Missing mandatory column: Hostname".
- **Start Pipeline** button remains disabled.
- No pipeline run is created.

### Scenario 3: Retry Feedback Loop

**Purpose**: Validate that test failures trigger agent retry with error context.

**Steps**:
1. Upload a UC document set that intentionally has ambiguous constraints (e.g., a field requiring "valid IP" but sample data contains both IPv4 and IPv6).
2. Start the pipeline.
3. Observe step 8 (test execution) — expect initial failure.
4. Watch the retry loop engage.

**Expected outcomes**:
- Step 8 shows "failed" then "retrying (attempt 1 of 3)".
- Progress banner updates with retry status.
- On successful retry: step transitions to "passed" and pipeline continues.
- On exhausted retries: pipeline shows "failed" with full attempt history in the report.
- HTML report contains all retry attempt details with agent prompts/responses.

### Scenario 4: Human-in-the-Loop (HIL) Interaction

**Purpose**: Validate that ambiguous data triggers HIL and pipeline pauses correctly.

**Steps**:
1. Upload a CIQ Excel with a column whose validation type cannot be auto-inferred (e.g., a column named "Service_Code" with mixed alphanumeric values).
2. Start the pipeline.
3. Observe HIL dialog appearing during steps 1-2.

**Expected outcomes**:
- Pipeline status changes to "paused_hil".
- A dialog appears showing: the field name, what the agent inferred, and asking for validation rule clarification.
- After providing a response (e.g., "regex: ^[A-Z]{2}\\d{4}$"), pipeline resumes.
- The validation rules YAML includes the user-specified constraint.

### Scenario 5: Pipeline Cancellation

**Purpose**: Validate that users can cancel a running pipeline and partial artifacts are preserved.

**Steps**:
1. Start a normal pipeline run.
2. While step 3 or later is in progress, click **Cancel**.

**Expected outcomes**:
- Pipeline status changes to "cancelled".
- Artifacts from completed steps are preserved in `data/runs/<run_id>/`.
- In-progress step artifacts are discarded.
- Run appears in History with "cancelled" status.

### Scenario 6: Run History and Filtering

**Purpose**: Validate run history browsing and filtering.

**Steps**:
1. Complete at least 3 pipeline runs (mix of completed, failed, cancelled).
2. Navigate to the **History** section.
3. Apply filters: by UC name, by status, by date range.

**Expected outcomes**:
- All runs appear in reverse chronological order.
- Filtering by status shows only matching runs.
- Filtering by UC name performs partial text match.
- Clicking a run shows full details and download option.

### Scenario 7: Regression Diff Detection

**Purpose**: Validate that re-running a UC flags config differences.

**Steps**:
1. Complete a successful pipeline run for UC "Test_VoLTE_Basic".
2. Run the pipeline again for the same UC (same documents).

**Expected outcomes**:
- Test report includes a "Regression Diff" section.
- If configs are identical: diff shows "No changes detected."
- If configs differ: diff highlights specific YAML changes between old and new.
- Pipeline is NOT blocked by the diff — it completes normally.

### Scenario 8: Settings Configuration

**Purpose**: Validate that settings persist and are respected by pipeline runs.

**Steps**:
1. Open the **Settings** section.
2. Change retry limit to 5.
3. Change the model for step 3 to a specific model.
4. Start a pipeline run.

**Expected outcomes**:
- Settings persist after page refresh.
- Pipeline retries up to 5 times (if failures occur).
- Step 3 uses the configured model (visible in run logs).
- API key is displayed masked (e.g., `sk-or-...xxxx`).

## Running Automated Tests

```bash
# Unit tests
pytest tests/unit/ -v

# Integration tests (requires JARs and OpenRouter API key)
pytest tests/integration/ -v

# Full end-to-end test
pytest tests/integration/test_pipeline_e2e.py -v
```

## Verification Checklist

- [ ] NiceGUI app starts and loads at http://localhost:8000
- [ ] Dark theme enabled by default with working toggle
- [ ] Document upload accepts .xlsx, .docx, .pdf, .txt files
- [ ] Upload validation catches invalid/incomplete documents
- [ ] Pipeline executes all 9 steps with real-time progress
- [ ] Sticky progress banner visible while scrolling
- [ ] HIL dialog appears for ambiguous data
- [ ] Test failures trigger retry with error context
- [ ] HTML and Excel reports generated for every run
- [ ] Artifacts downloadable as structured package
- [ ] Run history filterable by UC name, status, date
- [ ] Cleanup section allows selective artifact deletion
- [ ] Settings persist and are respected by pipeline
- [ ] API keys are masked in UI and not logged
- [ ] Few-shot knowledge base grows with successful runs
- [ ] Regression diff appears in reports for re-runs
