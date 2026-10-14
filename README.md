# CR Automation Agentic

An AI-powered pipeline framework for telecom Use Case (UC) automation. Generates cr-automation compatible YAML configurations (validation rules, JSON output templates, CLI automation workflows) from input documents using LLM agents.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    NiceGUI Web Interface (:8000)                │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐          │
│  │Dashboard │ │New Run   │ │Progress  │ │Outputs   │ ...       │
│  │          │ │(Upload)  │ │(Live)    │ │(Viewer)  │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘          │
├─────────────────────────────────────────────────────────────────┤
│                     Pipeline Orchestrator                       │
│  ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌─────┐ ┌───┐┌───┐┌───┐│
│  │St 1 │→│St 2 │→│St 3 │→│St 4 │→│St 5 │→│St 6 │→│ 7 ││ 8 ││ 9 ││
│  │Parse│ │Anal.│ │Valid.│ │JSON │ │Work │ │Sim. │ │Val││Tst││Rpt││
│  │ LLM │ │ LLM │ │ LLM │ │ LLM │ │ LLM │ │ LLM │ │Det││Det││Det││
│  └─────┘ └─────┘ └──┬──┘ └──┬──┘ └──┬──┘ └──┬──┘ └───┘└───┘└───┘│
│                    Milestone Review Pauses (configurable)        │
├─────────────────────────────────────────────────────────────────┤
│  liteLLM/OpenRouter  │  SQLite DB  │  Artifact Store (data/)    │
│  (Gemini/DeepSeek/   │  (settings, │  (uploads, runs, logs,     │
│   Claude/GPT)        │   runs, HIL)│   knowledge base)          │
└─────────────────────────────────────────────────────────────────┘
```

## Pipeline Steps

| Step | Agent | Type | Output |
|------|-------|------|--------|
| 1 | Document Parser | LLM | Intermediate Representation |
| 2 | UC Analyzer | LLM | Enriched IR + HIL requests |
| 3 | Validation Rules Gen | LLM | `<UC>_validation-rules.yaml` |
| 4 | JSON Output Gen | LLM | `<UC>_json-output.yaml` |
| 5 | Workflow Gen | LLM | `<UC>.yaml` |
| 6 | Simulator Config Gen | LLM | `<UC>_simulator-config.yaml` |
| 7 | Schema Validator | Deterministic | Validation warnings |
| 8 | Test Executor | Deterministic | Test results (JAR-based) |
| 9 | Report Generator | Deterministic | HTML + Excel reports |

Steps 3-6 are **milestone steps** — pipeline pauses after each for user review (configurable).

## Quick Start

### Prerequisites
- Python 3.11+
- OpenRouter API key ([get one here](https://openrouter.ai/keys))

### Setup
```bash
git clone <repo-url>
cd cr_automation-agentic
python -m venv .venv

# Windows (Git Bash)
source .venv/Scripts/activate

# Linux/Mac
source .venv/bin/activate

pip install -e ".[dev]"
```

### Configure API Key
```bash
# Option 1: .env file (recommended — survives DB resets)
echo "OPENROUTER_API_KEY=sk-or-v1-your-key" > .env

# Option 2: Via Settings UI after starting the server
```

### Run
```powershell
# PowerShell
.\start.ps1

# Git Bash
./start.sh
```

Open http://localhost:8000

### Stop
```powershell
.\stop.ps1    # PowerShell
./stop.sh     # Git Bash
```

## UI Sections

| Section | Description |
|---------|-------------|
| **Dashboard** | Pipeline runs count, success rate, avg duration |
| **New Pipeline Run** | Select node type + activity, upload documents, start pipeline |
| **Pipeline Progress** | Real-time step tracking, animated progress bar, milestone reviews |
| **Output Documents** | Browse generated artifacts by node/activity, view/download files |
| **Run History** | Filter by node/activity/status, view details, re-run pipelines |
| **Artifact Cleanup** | Delete runs and artifacts with filters |
| **Settings** | API key, LLM model selection (per-step), retry config, JAR paths |

## Input Documents

| Document | Format | Required |
|----------|--------|----------|
| CIQ Excel | `.xlsx` / `.xls` | Yes |
| MOP Document | `.docx` / `.pdf` / `.txt` | Yes |
| Sample I/O XMLs | `.xml` (multiple) | Yes |
| Constraints | `.txt` / `.yaml` / `.json` / `.csv` | No |
| Command Outputs | `.txt` | Yes |
| Additional Documents | any | No |

## Output Artifacts

File naming: `<NODE_TYPE>_<ACTIVITY_NAME>_<suffix>`

| Artifact | File | Schema |
|----------|------|--------|
| Validation Rules | `*_validation-rules.yaml` | `schemas/validation_rules_schema.yaml` |
| JSON Output | `*_json-output.yaml` | `schemas/json-output-schema.yaml` |
| Workflow | `*.yaml` (base name) | `schemas/cli-automation-workflow-schema_v1.2.yaml` |
| Simulator Config | `*_simulator-config.yaml` | `schemas/simulator_config.schema.yaml` |
| HTML Report | `*_report.html` | — |
| Excel Report | `*_report.xlsx` | — |

## LLM Models

Default: `openrouter/google/gemini-2.5-flash` (configurable per-step)

| Tier | Models | Cost (input/output per 1M tokens) |
|------|--------|-----------------------------------|
| Best Value | Gemini 2.5 Flash, DeepSeek V3, Gemini 2.5 Flash Lite | $0.075-$0.15 / $0.28-$0.60 |
| Premium | Claude Sonnet 4, Gemini 2.5 Pro, GPT-4o | $1.25-$3 / $10-$15 |
| Free | Gemini 2.5 Flash Free, Llama 3.3 70B, DeepSeek V3 Free | Free (rate limited) |

## Project Structure

```
cr_automation-agentic/
├── src/
│   ├── agents/          # 9 pipeline agents + liteLLM integration
│   ├── core/            # Pipeline engine, models, config, events, HIL, retry
│   ├── parsers/         # CIQ Excel, MOP, constraints, command output parsers
│   ├── knowledge/       # Few-shot store + embedding similarity
│   ├── storage/         # SQLite DB + artifact file store
│   ├── testing/         # Regression diff, report templates
│   ├── ui/              # NiceGUI app + page sections
│   └── prompts/         # Externalized YAML prompt templates
├── schemas/             # cr-automation YAML schema references
├── config/              # pipeline_config.yaml, models.yaml
├── data/                # Runtime data (uploads, runs, logs, DB)
├── tests/               # Test fixtures
├── start.ps1 / start.sh # Server start scripts
├── stop.ps1 / stop.sh   # Server stop scripts
└── .env                 # API key (not committed)
```

## Key Features

- **Milestone Review** — pause after each YAML generation step to review artifacts before continuing
- **Human-in-the-Loop** — pipeline pauses for ambiguous fields requiring human clarification
- **Re-run Support** — re-run any previous pipeline from Run History with same inputs
- **Smart Retry** — LLM steps retry with error context; deterministic steps run once
- **Few-shot Learning** — successful configs stored as examples for future runs
- **Real-time Progress** — animated progress bar, step-by-step tracking, WebSocket events
- **Cascading Filters** — filter by node type and activity across all sections
- **Settings Persistence** — all settings saved to SQLite, survive restarts
