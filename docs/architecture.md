# CR Automation Agentic — Architecture Document

**Version**: 2.0
**Framework**: CR Automation Agentic Pipeline
**Pipeline**: 10 steps, 10 agents (6 LLM + 4 deterministic)

---

## 1. System Overview

CR Automation Agentic is an AI-powered pipeline framework that generates telecom Use Case (UC) configuration artifacts from input documents. It wraps the existing **cr-automation** Java framework (ciq-processor, cli-automation-standalone, mockwire-simulator, sbc-python-scripts) with an intelligent agent layer that uses Large Language Models to automate configuration generation and QA testing.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Web Browser (User)                           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP/WebSocket (:8000)
┌──────────────────────────────▼──────────────────────────────────────┐
│                     NiceGUI Application Layer                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │Dashboard │ │New Run   │ │Progress  │ │Outputs   │ │Settings  │ │
│  │(stats)   │ │(upload)  │ │(10-step) │ │(viewer)  │ │(config)  │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
├─────────────────────────────────────────────────────────────────────┤
│                     Pipeline Orchestrator (10 steps)                │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Row 1: CIQ Processing                                        │ │
│  │  [1]→[2]→[3]★→[4]★→[5]★                                     │ │
│  │  Parse  Analyze  ValRules  JSONOut  CIQ-QA                    │ │
│  │  LLM    LLM      LLM      LLM     JAR+QA                     │ │
│  │                     ↓                                          │ │
│  │  Row 2: Execution Pipeline                                    │ │
│  │  [6]★→[7]★→[8]★→[9]★→[10]                                   │ │
│  │  Workflow MOP-Test SimCfg  Exec-QA  Report                    │ │
│  │  LLM     JAR      LLM     JAR+QA   Determ.                   │ │
│  │                   ★ = Milestone Review Pause                   │ │
│  └────────────────────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐ ┌─────────────┐ ┌─────────────┐ ┌─────────────┐  │
│  │  liteLLM    │ │  SQLite DB  │ │ Artifact    │ │ Knowledge   │  │
│  │  OpenRouter │ │  (aiosqlite)│ │ Store       │ │ Base        │  │
│  │  API Layer  │ │             │ │ (filesystem)│ │ (embeddings)│  │
│  └─────────────┘ └─────────────┘ └─────────────┘ └─────────────┘  │
├─────────────────────────────────────────────────────────────────────┤
│  ┌─────────────────────────────────────────────────────────────┐   │
│  │              cr-automation Java Framework                    │   │
│  │   ciq-processor.jar         cli-automation-standalone.jar    │   │
│  │   (validation/JSON)         (MOP generation/execution)       │   │
│  │   mockwire-simulator        sbc-python-scripts               │   │
│  │   (SSH mock for testing)    (NETCONF XML generation)         │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Pipeline — 10 Steps, 10 Agents

| Step | Agent | Type | Output | Milestone |
|------|-------|------|--------|-----------|
| 1 | `DocParserAgent` | LLM | Intermediate Representation | — |
| 2 | `UCAnalyzerAgent` | LLM | Enriched IR + HIL requests | — |
| 3 | `ValidationGenAgent` | LLM | `<UC>_validation-rules.yaml` | ★ Review |
| 4 | `JsonTemplateAgent` | LLM | `<UC>_json-output.yaml` | ★ Review |
| 5 | `TestExecutor(ciq)` + `ciq_qa` | JAR + QA | QA report (HTML/Excel) | ★ Review |
| 6 | `WorkflowGenAgent` | LLM | `<UC>.yaml` | ★ Review |
| 7 | `MopGenTester` | JAR | MOP HTML document | ★ Review |
| 8 | `SimulatorSetupAgent` | LLM | MockWire configs + responses | ★ Review |
| 9 | `TestExecutor(exec)` | JAR + QA | Execution QA report | ★ Review |
| 10 | `ReportGenerator` | Deterministic | Final HTML + Excel report | — |

### Step Details

**Steps 1-2: Document Parsing & Analysis**
- Parse CIQ Excel (per-sheet headers + sample rows), MOP (.docx/.pdf), constraints, command outputs
- Build Intermediate Representation (IR) with field types, sheet relationships
- HIL (Human-in-the-Loop) requests for ambiguous data

**Step 3: Validation Rules Generation**
- LLM generates `validation-rules.yaml` from CIQ data + schema reference
- Post-generation validation: YAML syntax, column name matching against CIQ, completeness check, required-vs-data check
- Auto-retry with error context on validation failure (up to 3 attempts)

**Step 4: JSON Output Generation**
- LLM generates `json-output.yaml` (CIQ-to-JSON mapping template)
- YAML quoting auto-fix for dotted references
- Syntax validation with retry

**Step 5: CIQ Validation QA Cycle**
- Generates test cases from validation rules (positive + negative):
  - TC01: Valid CIQ (happy path)
  - TC02: Valid CIQ + JSON output generation
  - TC03-N: Missing mandatory columns, invalid data types, pattern violations, cross-sheet rule violations
  - TC-last: Optional fields blank (skips columns protected by one_of/minOnePerGroup)
- Creates modified CIQ Excel files for each negative test
- Executes each via `ciq-processor.jar --mode ciq-validate`
- Compares expected vs actual → QA execution report
- Output: `qa/test-cases.xlsx`, `qa/qa-execution-report.html/.xlsx`, `qa/test_data/`, `qa/results/`
- Failed TCs fed back to Step 3 LLM on re-run

**Step 6: Workflow Generation**
- LLM generates cli-automation workflow YAML v1.2 with 5 mandatory phases:
  1. Pre-node health check
  2. Backup
  3. Activity configuration (maps every MOP step)
  4. Post-node health check
  5. Rollback (conditional)
- Auto-detects connection type from CIQ (NIAM proxy vs direct SSH)
- Uses JSON output structure as variable context
- Validates: YAML syntax, phases-as-map structure, minimum step count
- Auto-fixes: tabs→spaces, phases-as-list→map conversion

**Step 7: MOP Generation Test**
- Runs `cli-automation-standalone.jar generate` with workflow YAML + JSON input
- Validates the workflow can produce a proper MOP HTML document
- Output: `mop_output/*.html`
- Milestone shows generation report with file list and JAR output

**Step 8: Simulator Config Generation**
- Parses workflow YAML → extracts all SSH nodes and send commands
- LLM generates realistic mock responses per command (concrete values, no placeholders)
- Builds MockWire config YAMLs: auth, NIAM (if applicable), command→response mappings
- Output: `simulator/config/<UC>_SIMULATOR_CONFIG.yaml`, `simulator/data/*.txt`
- Milestone shows HTML overview of all configs + response files

**Step 9: Execution Test QA**
- Runs workflow via `cli-automation-standalone.jar execute` against MockWire simulator
- QA test cycle similar to Step 5

**Step 10: Final Report**
- Generates consolidated HTML + Excel pipeline report

---

## 3. Technology Stack

### Core Runtime
| Component | Library | Purpose |
|-----------|---------|---------|
| Web Framework | NiceGUI 3.17+ | Single-page reactive UI with WebSocket |
| Data Validation | Pydantic 2.x | Type-safe models for pipeline entities |
| Python | 3.14+ | Async/await throughout |

### LLM Integration
| Component | Library | Purpose |
|-----------|---------|---------|
| LLM Router | liteLLM | Unified API for 200+ providers via OpenRouter |
| Default Model | Gemini 2.5 Flash | Cost-effective structured output |

### Java Integration
| Component | JAR | Purpose |
|-----------|-----|---------|
| CIQ Processor | `ciq-processor-*-cli.jar` | CIQ validation + JSON output generation |
| CLI Automation | `cli-automation-standalone.jar` | MOP generation + workflow execution |
| MockWire | `mockwire.jar` | SSH mock simulator for testing |
| Python Scripts | `sbc-python-scripts/` | NETCONF XML generation/rollback/comparison |

### Storage
| Component | Purpose |
|-----------|---------|
| SQLite (aiosqlite) | Pipeline runs, step results, settings, HIL requests |
| Filesystem | `data/runs/<run_id>/` — artifacts, QA reports, simulator configs |

---

## 4. Source Modules

```
src/
├── agents/                    # 10 agents + QA module
│   ├── __init__.py            # call_llm(), STEP_NAMES, LLM_STEPS
│   ├── doc_parser_agent.py    # Step 1: Parse documents → IR
│   ├── uc_analyzer_agent.py   # Step 2: Enrich IR
│   ├── validation_gen_agent.py # Step 3: validation-rules.yaml + CIQ validation
│   ├── json_template_agent.py # Step 4: json-output.yaml + YAML fix
│   ├── ciq_qa.py              # Step 5: QA test case gen + execution + reporting
│   ├── workflow_gen_agent.py  # Step 6: workflow.yaml (5 phases)
│   ├── mop_gen_tester.py      # Step 7: MOP generation test via JAR
│   ├── simulator_setup.py     # Step 8: MockWire config generation
│   ├── test_executor.py       # Steps 5,9: JAR test execution engine
│   └── report_generator.py    # Step 10: Final HTML + Excel reports
│
├── core/
│   ├── pipeline.py            # PipelineOrchestrator (10-step, milestones, resume)
│   ├── models.py              # Pydantic models (steps 1-10)
│   ├── config.py              # ConfigManager (YAML + DB overlay)
│   ├── events.py              # Async event bus (milestone_reached, etc.)
│   ├── retry.py               # RetryManager (test failure + infra retry)
│   └── hil.py                 # Human-in-the-Loop handler
│
├── parsers/                   # Document parsers (CIQ, MOP, constraints, cmd outputs)
├── knowledge/                 # Few-shot store + embedding similarity
├── storage/                   # SQLite DB + artifact store
└── ui/
    ├── app.py                 # NiceGUI app (sidebar 160px, content ~88%)
    └── pages/
        ├── progress_section.py    # Two-row 10-step tracker with arrow connectors
        ├── upload_section.py      # Document upload + UC identity
        ├── history_section.py     # Run history with error detail popups
        ├── output_viewer_section.py
        ├── settings_section.py
        └── cleanup_section.py
```

---

## 5. Milestone Review & QA Flow

### Milestone Behavior
- Pipeline pauses after steps 3, 4, 5, 6, 7, 8, 9
- **All tests pass**: Shows `[Continue]` `[Re-run]` `[Cancel]`
- **Tests fail** (steps 5, 7, 9): Hides `[Continue]`, shows `[Skip & Continue]` `[Re-run]` `[Cancel]`
- Re-run mapping: Step 5 → restart from Step 3, Steps 7/9 → restart from Step 6

### QA Feedback Loop (Step 5 → Step 3)
1. Step 5 QA finds failures (e.g., "optional column causes one_of violation")
2. User clicks "Re-run Steps 3-5"
3. Failed test cases passed as `qa_feedback` to Step 3 LLM prompt
4. LLM regenerates validation rules addressing the feedback
5. Steps 3-4-5 re-execute with improved rules

### Orphan Recovery (Server Restart)
- `resume_run(run_id, from_step)` rebuilds step_data from saved artifacts/IR
- Live check `_milestone_events` at click time (not stale flags)
- Resume from correct step, not step 1

---

## 6. Output Artifacts Per Run

```
data/runs/<run_id>/
├── <UC>_validation-rules.yaml      # Step 3
├── <UC>_json-output.yaml           # Step 4
├── qa/                             # Step 5 QA
│   ├── test-cases.xlsx
│   ├── qa-execution-report.html
│   ├── qa-execution-report.xlsx
│   ├── test_data/TC01_*.xlsx ...
│   └── results/TC01/ TC02/ ...
├── ciq_output/                     # Step 5 JAR output
│   ├── <UC>_VALIDATION_REPORT.json/.html/.xlsx
│   └── <UC>.json
├── <UC>.yaml                       # Step 6 workflow
├── mop_output/*.html               # Step 7 MOP documents
├── simulator/                      # Step 8
│   ├── config/<UC>_SIMULATOR_CONFIG.yaml
│   ├── data/*.txt (response files)
│   └── simulator_manifest.yaml
└── <UC>_report.html/.xlsx          # Step 10 final report
```

---

## 7. Configuration

### Priority Cascade
```
config/pipeline_config.yaml  → config/models.yaml  → SQLite settings  → .env  → env vars
```

### Key Settings
| Setting | Default | Description |
|---------|---------|-------------|
| `ciq_processor_jar` | `./lib/ciq-processor.jar` | CIQ validation JAR path |
| `cli_automation_jar` | `./lib/cli-automation-standalone.jar` | CLI automation JAR path |
| `test_retry_limit` | 3 | Max LLM retries on test failure |
| `default_model` | `openrouter/google/gemini-2.5-flash` | Default LLM model |

---

## 8. Related Projects

| Project | Path | Purpose |
|---------|------|---------|
| ciq-processor | `D:/Nokia/workspace/ciq-processor` | CIQ Excel validation + JSON generation |
| cli-automation-standalone | `D:/Nokia/workspace/cli-automation-standalone` | MOP generation + workflow execution |
| mockwire-simulator | `D:/Nokia/workspace/mockwire/mockwire-simulator` | SSH mock simulator for testing |
| sbc-python-scripts | `D:/Nokia/workspace/sbc-python-scripts` | NETCONF XML config generation/rollback |
