# CR Automation Agentic — Architecture Document

**Version**: 1.0
**Framework**: CR Automation Agentic Pipeline
**Codebase**: 46 Python source files, ~6,400 lines of code

---

## 1. System Overview

CR Automation Agentic is an AI-powered pipeline framework that generates telecom Use Case (UC) configuration artifacts from input documents. It wraps the existing **cr-automation** Java framework (ciq-processor, cli-automation-standalone) with an intelligent agent layer that uses Large Language Models to automate configuration generation.

```
┌─────────────────────────────────────────────────────────────────────┐
│                        Web Browser (User)                           │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ HTTP/WebSocket (:8000)
┌──────────────────────────────▼──────────────────────────────────────┐
│                     NiceGUI Application Layer                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ │
│  │Dashboard │ │New Run   │ │Progress  │ │Outputs   │ │Settings  │ │
│  │(stats)   │ │(upload)  │ │(live)    │ │(viewer)  │ │(config)  │ │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ │
├─────────────────────────────────────────────────────────────────────┤
│                     Pipeline Orchestrator                           │
│  ┌────────────────────────────────────────────────────────────────┐ │
│  │  Step 1 → Step 2 → Step 3* → Step 4* → Step 5* → Step 6*    │ │
│  │  (Parse)  (Analyze) (Valid.) (JSON)   (Workflow) (Simulator)  │ │
│  │     LLM      LLM      LLM     LLM       LLM       LLM       │ │
│  │                    * = Milestone Review Pause                  │ │
│  │                                                                │ │
│  │  Step 7 → Step 8 → Step 9                                    │ │
│  │  (Schema) (Test)   (Report)  ← Deterministic (no LLM)        │ │
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
│  │   ciq-processor.jar    cli-automation-standalone.jar         │   │
│  │   (validation/JSON)    (workflow execution/testing)          │   │
│  └─────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 2. Technology Stack

### 2.1 Core Runtime

| Component | Library | Version | Purpose |
|-----------|---------|---------|---------|
| **Web Framework** | NiceGUI | 3.17.1 | Single-page web UI with reactive components, WebSocket real-time updates |
| **ASGI Server** | Uvicorn | 0.54.0 | Async HTTP/WebSocket server underneath NiceGUI |
| **API Framework** | FastAPI | 0.141.1 | REST API layer (bundled with NiceGUI) |
| **Data Validation** | Pydantic | 2.13.5 | Type-safe models for pipeline entities, config validation |
| **Python** | CPython | 3.14+ | Async/await throughout, type hints |

### 2.2 LLM Integration

| Component | Library | Version | Purpose |
|-----------|---------|---------|---------|
| **LLM Router** | liteLLM | 1.104.0 | Unified API for 200+ LLM providers via OpenRouter |
| **Default Model** | Gemini 2.5 Flash | — | Cost-effective, fast structured output generation |
| **Provider** | OpenRouter | — | Model routing, fallback, rate limiting |

**Model Selection** (configurable per-step):

| Tier | Models | Cost (in/out per 1M tokens) |
|------|--------|----------------------------|
| Best Value | Gemini 2.5 Flash, DeepSeek V3 | $0.075–$0.15 / $0.28–$0.60 |
| Premium | Claude Sonnet 4, Gemini 2.5 Pro, GPT-4o | $1.25–$3 / $10–$15 |
| Free | Gemini 2.5 Flash Free, Llama 3.3 70B | Free (rate limited) |

### 2.3 Document Processing

| Component | Library | Version | Purpose |
|-----------|---------|---------|---------|
| **Excel Parser** | openpyxl | 3.1.5 | CIQ workbook parsing (header-name-based, all sheets) |
| **Word Parser** | python-docx | 1.2.0 | MOP document extraction (.docx) |
| **PDF Parser** | PyPDF2 | 3.0.1 | MOP document extraction (.pdf) |
| **YAML Processing** | ruamel.yaml | 0.19.1 | Schema loading, config parsing (preserves comments) |
| **JSON Schema** | jsonschema | 4.26.0 | Structural validation of generated configs |

### 2.4 Storage & Persistence

| Component | Library | Version | Purpose |
|-----------|---------|---------|---------|
| **Database** | aiosqlite | 0.22.1 | Async SQLite — pipeline runs, step results, settings, HIL requests |
| **Artifact Store** | filesystem | — | `data/runs/<run_id>/` — generated YAML, reports, artifacts |
| **Config Store** | SQLite `settings` table | — | Persisted UI settings (API key, model, retry limits) |
| **UC Options** | JSON file | — | `data/uc_options.json` — node types + activity names |

### 2.5 Knowledge Base & Embeddings

| Component | Library | Version | Purpose |
|-----------|---------|---------|---------|
| **Embedding Model** | sentence-transformers | 6.1.0 | `all-MiniLM-L6-v2` for UC similarity matching |
| **Few-shot Store** | filesystem + SQLite | — | Successful UC configs stored as examples for future runs |

### 2.6 Reporting

| Component | Library | Version | Purpose |
|-----------|---------|---------|---------|
| **HTML Reports** | Jinja2 | 3.1.6 | Templated pipeline test reports |
| **Excel Reports** | XlsxWriter | 3.2.9 | Structured Excel report generation |

### 2.7 Java Integration

| Component | JAR | Purpose |
|-----------|-----|---------|
| **CIQ Processor** | ciq-processor.jar | CIQ validation, JSON output generation |
| **CLI Automation** | cli-automation-standalone.jar | Workflow execution, testing against simulator |

Invoked via `subprocess.run()` — Java JARs are never modified.

---

## 3. Module Architecture

### 3.1 Source Modules

```
src/
├── agents/              # 10 files, ~1,055 lines
│   ├── __init__.py      # call_llm(), get_model_for_step(), AgentStep protocol
│   ├── doc_parser_agent.py      # Step 1: Parse CIQ/MOP/constraints → IR
│   ├── uc_analyzer_agent.py     # Step 2: Enrich IR, detect HIL needs
│   ├── validation_gen_agent.py  # Step 3: Generate validation_rules.yaml
│   ├── json_template_agent.py   # Step 4: Generate json-output.yaml
│   ├── workflow_gen_agent.py    # Step 5: Generate workflow.yaml
│   ├── script_gen_agent.py      # Step 6: Generate simulator_config.yaml + scripts
│   ├── schema_validator.py      # Step 7: Structural YAML validation
│   ├── test_executor.py         # Step 8: Run Java JARs for testing
│   └── report_generator.py      # Step 9: HTML + Excel reports
│
├── core/                # 10 files, ~1,737 lines
│   ├── pipeline.py      # PipelineOrchestrator — main execution engine
│   ├── models.py        # Pydantic models (PipelineRun, StepResult, UCDocumentSet, etc.)
│   ├── config.py        # ConfigManager — YAML + DB settings loading
│   ├── events.py        # Async event bus (PipelineEvent, subscribe/publish)
│   ├── hil.py           # Human-in-the-Loop handler (batch request + wait)
│   ├── retry.py         # RetryManager — test failure + infrastructure retry
│   ├── intermediate_repr.py  # Intermediate Representation Pydantic models
│   ├── document_validator.py  # Upload-time document format validation
│   └── logging_config.py      # Structured JSON logging
│
├── parsers/             # 5 files, ~528 lines
│   ├── ciq_parser.py           # Excel workbook parser (header-name-based)
│   ├── mop_parser.py           # MOP document parser (.docx/.pdf/.txt)
│   ├── constraints_parser.py   # Constraints file parser (structured + free-form)
│   └── command_output_parser.py # CLI session log parser
│
├── knowledge/           # 3 files, ~181 lines
│   ├── few_shot_store.py       # Store/retrieve successful UC configs
│   └── similarity.py           # Embedding-based UC similarity matching
│
├── storage/             # 3 files, ~318 lines
│   ├── db.py                   # SQLite database (async, 9 tables)
│   └── artifact_store.py       # File-based artifact storage + packaging
│
├── testing/             # 3 files, ~118 lines
│   ├── regression_diff.py      # YAML diff against previous successful configs
│   └── report/templates/       # Jinja2 HTML report templates
│
├── ui/                  # 11 files, ~2,479 lines
│   ├── app.py                  # Main NiceGUI app (layout, routing, CSS)
│   ├── theme.py                # Dark/light theme management
│   └── pages/
│       ├── upload_section.py        # Document upload + UC identity selection
│       ├── progress_section.py      # Live pipeline tracking + milestone review
│       ├── output_viewer_section.py # Artifact browser with view/download
│       ├── history_section.py       # Run history with filters + details
│       ├── cleanup_section.py       # Artifact deletion
│       ├── settings_section.py      # Tabbed settings (API, models, retry, modules)
│       └── hil_dialog.py           # Human-in-the-Loop dialog
│
└── prompts/             # Externalized YAML prompt templates (steps 1-6)
```

### 3.2 Schema Files

```
schemas/
├── validation_rules_schema.yaml              # cr-automation CIQ validation rules
├── json-output-schema.yaml                   # cr-automation JSON output template
├── cli-automation-workflow-schema_v1.2.yaml  # cr-automation CLI workflow v1.2
└── simulator_config.schema.yaml             # Mock simulator configuration
```

### 3.3 Configuration Files

```
config/
├── pipeline_config.yaml   # Default model, retry limits, JAR paths, storage paths
└── models.yaml            # Per-step model assignments
```

### 3.4 Runtime Data

```
data/
├── pipeline.db            # SQLite database (9 tables)
├── uc_options.json        # Persisted node types + activity names
├── uploads/               # Uploaded input documents
├── runs/<run_id>/         # Generated artifacts per pipeline run
├── logs/                  # Server logs (timestamped)
└── knowledge/             # Few-shot knowledge base
```

---

## 4. Database Schema

9 tables in SQLite (`data/pipeline.db`):

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `pipeline_runs` | Pipeline execution records | id, document_set_id, status, current_step, error_summary |
| `uc_document_sets` | Uploaded document metadata | id, uc_name, ciq/mop/xml/constraints paths |
| `step_results` | Per-step execution results | run_id, step_number, status, error_message, retry_count |
| `retry_attempts` | Retry history | step_result_id, attempt_number, error_context, outcome |
| `hil_requests` | Human-in-the-Loop requests | run_id, step_number, question, user_response, status |
| `generated_configs` | Output artifact metadata | run_id, config_type, file_path, schema_valid |
| `test_reports` | Test report paths | run_id, html_report_path, passed/failed counts |
| `settings` | Persisted UI settings | key, value, updated_at |
| `knowledge_base_entries` | Few-shot examples | uc_name, embedding, config paths |

---

## 5. Pipeline Execution Flow

### 5.1 Step Execution

```
User uploads docs → Start Pipeline
    │
    ▼
Step 1: Doc Parser (LLM)
    │ Parse CIQ/MOP/constraints/command outputs
    │ → Intermediate Representation (IR)
    │ → HIL requests if ambiguous fields found
    ▼
Step 2: UC Analyzer (LLM)
    │ Enrich IR with field types, validation rules
    │ → HIL requests for complex constraints
    │ Flatten IR into step_data for generation agents
    ▼
Step 3: Validation Rules Generator (LLM) ★ MILESTONE
    │ Schema: validation_rules_schema.yaml
    │ → <UC>_validation-rules.yaml
    │ → Pause for user review (if enabled)
    ▼
Step 4: JSON Output Generator (LLM) ★ MILESTONE
    │ Schema: json-output-schema.yaml
    │ → <UC>_json-output.yaml
    │ → Pause for user review (if enabled)
    ▼
Step 5: Workflow Generator (LLM) ★ MILESTONE
    │ Schema: cli-automation-workflow-schema_v1.2.yaml
    │ → <UC>.yaml
    │ → Pause for user review (if enabled)
    ▼
Step 6: Simulator Config Generator (LLM) ★ MILESTONE
    │ Schema: simulator_config.schema.yaml
    │ → <UC>_simulator-config.yaml + optional Python scripts
    │ → Pause for user review (if enabled)
    ▼
Step 7: Schema Validator (Deterministic, no retry)
    │ Structural YAML validation (parseable + expected keys)
    │ → Warnings only, does not block pipeline
    ▼
Step 8: Test Executor (Deterministic, no retry)
    │ Invoke ciq-processor.jar + cli-automation-standalone.jar
    │ → Test results (pass/fail per assertion)
    ▼
Step 9: Report Generator (Deterministic, no retry)
    │ → <UC>_report.html + <UC>_report.xlsx
    ▼
Pipeline Complete → Artifacts available in Output Documents
```

### 5.2 Retry Strategy

| Step Type | Retry Behavior |
|-----------|---------------|
| Steps 1-6 (LLM) | Test failure: up to 3 retries with error context fed back to LLM |
| Steps 1-6 (LLM) | Infrastructure (API timeout/auth): up to 3 retries with exponential backoff |
| Steps 1-2 | `needs_hil` status treated as success (valid output, just needs human input) |
| Steps 7-9 | No retries — deterministic steps run once |

### 5.3 Milestone Review

When enabled (checkbox on New Pipeline Run, default: on):
- Pipeline pauses after steps 3, 4, 5, 6
- User sees artifact name + View button (opens fullscreen dialog)
- Three options: **Continue** (accept) | **Re-run Step** (regenerate with LLM) | **Cancel Pipeline**
- Pipeline status: `paused_milestone` during review

### 5.4 Human-in-the-Loop (HIL)

- Steps 1-2 can produce HIL requests for ambiguous fields
- Pipeline status: `paused_hil` — waits indefinitely
- Batch HIL: multiple questions presented as a form
- User responds via HIL dialog in the UI

---

## 6. UI Architecture

### 6.1 Layout

```
┌─────────────────────────────────────────────────────┐
│  Header (48px) — Logo, version badge, theme toggle  │
├──────────┬──────────────────────────────────────────┤
│ Sidebar  │  Content Area (scrollable)               │
│ (220px)  │                                          │
│          │  Active section content                   │
│ Dashboard│  (only one visible at a time)            │
│ New Run  │                                          │
│ Progress │                                          │
│ Outputs  │                                          │
│ History  │                                          │
│ Cleanup  │                                          │
│ Settings │                                          │
│          │                                          │
│ v0.1.0   │                                          │
└──────────┴──────────────────────────────────────────┘
```

### 6.2 Section Details

| Section | Key Components |
|---------|---------------|
| **Dashboard** | Live stat cards (total runs, success rate, avg duration) from DB |
| **New Pipeline Run** | Node type dropdown (with OTHER + custom), activity name input (autocomplete), 6 upload cards (CIQ, MOP, XMLs, constraints, cmd outputs, additional), milestone review checkbox |
| **Pipeline Progress** | Animated progress bar (indeterminate → determinate), 9-step visual tracker, milestone review panel with View artifact dialog, completion links |
| **Output Documents** | Node/Activity filter dropdowns, run selector, collapsed file groups, per-file View (fullscreen dialog) + Download buttons |
| **Run History** | Node/Activity/Status cascade filters, table with Details/Outputs/Re-run buttons, inline detail panel (input docs, step results, output artifacts, errors) |
| **Artifact Cleanup** | Node/Activity filters, table with Delete button, confirmation dialog, cascade DB cleanup |
| **Settings** | 4 tabs: API & Models (key + default model dropdown), Per-Step Models (6 individual dropdowns), Retry Policy (3 cards), External Modules (JARs + simulator) |

### 6.3 Real-time Updates

- **WebSocket**: NiceGUI maintains persistent WebSocket connection
- **Event Bus**: `PipelineEvent` published on step_started/completed/failed/retry/milestone/run_completed
- **Progress Section**: Subscribes to events for active run, updates UI reactively
- **Reconnect**: `reconnect_timeout=60` — auto-reconnects on connection drop
- **Navigate Hooks**: Sections auto-refresh data when navigated to (history, outputs, cleanup)

---

## 7. Data Flow

### 7.1 Input Documents

| Document | Parser | Output |
|----------|--------|--------|
| CIQ Excel (.xlsx) | `ciq_parser.py` | Headers + rows + field types |
| MOP (.docx/.pdf/.txt) | `mop_parser.py` | Command steps + expected outputs |
| Constraints (.txt/.yaml) | `constraints_parser.py` | Field constraints + business rules |
| Command Outputs (.txt) | `command_output_parser.py` | Command-response pairs |
| Sample I/O XMLs (.xml) | Passed through | Reference for config generation |

### 7.2 Output Artifacts

| Artifact | Naming Pattern | Consumer |
|----------|---------------|----------|
| Validation Rules | `<UC>_validation-rules.yaml` | ciq-processor.jar |
| JSON Output | `<UC>_json-output.yaml` | ciq-processor.jar, mop-generator, command-executor |
| Workflow | `<UC>.yaml` | cli-automation-standalone.jar |
| Simulator Config | `<UC>_simulator-config.yaml` | Mock simulator for testing |
| HTML Report | `<UC>_report.html` | Human review |
| Excel Report | `<UC>_report.xlsx` | Human review |

### 7.3 Configuration Cascade

```
config/pipeline_config.yaml    (base defaults)
        ↓
config/models.yaml             (per-step model overrides)
        ↓
SQLite settings table          (UI-saved overrides — highest priority)
        ↓
.env file                      (API key fallback)
        ↓
Environment variables          (API key last resort)
```

---

## 8. Security Considerations

- **API keys**: Never logged, masked as `sk-...xxxx` in display, stored in DB settings table
- **File uploads**: Saved with UUID prefix to prevent collisions, validated format before pipeline
- **Java subprocess**: Commands constructed from config, not user input — no injection risk
- **WebSocket**: NiceGUI handles auth via session cookies
- **No external network access**: Only outbound calls are to OpenRouter API

---

## 9. Deployment

### 9.1 Local Development

```powershell
.\start.ps1   # PowerShell
./start.sh    # Git Bash
```

### 9.2 Production Considerations

- Set `OPENROUTER_API_KEY` via environment variable or `.env` file
- Place Java JARs in `./lib/` directory
- Configure JAR paths in Settings → External Modules
- SQLite is suitable for single-user; for multi-user, consider PostgreSQL migration
- NiceGUI runs on Uvicorn — can be fronted with nginx for TLS

### 9.3 Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `OPENROUTER_API_KEY` | LLM API access | Yes (or set via UI) |
| `HF_HUB_DISABLE_SYMLINKS_WARNING` | Suppress HF warning | No (auto-set) |
| `PYTHONWARNINGS` | Suppress Python warnings | No (auto-set in start scripts) |
