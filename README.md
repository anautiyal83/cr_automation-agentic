# CR Automation Agentic

An AI-powered pipeline framework for telecom Use Case (UC) automation. Generates cr-automation compatible YAML configurations (validation rules, JSON output templates, CLI automation workflows) from input documents using LLM agents, with built-in QA testing cycles.

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                   NiceGUI Web Interface (:8000)                   │
│  Dashboard │ New Run │ Progress │ Outputs │ History │ Settings    │
├──────────────────────────────────────────────────────────────────┤
│                  Pipeline Orchestrator (10 steps)                 │
│                                                                   │
│  Row 1: CIQ Processing                                           │
│  [1]→►[2]→►[3]★→►[4]★→►[5]★                                    │
│  Parse Analyze ValRules JSONOut  CIQ-QA                          │
│  LLM   LLM     LLM     LLM     JAR+QA                           │
│                    ↓                                              │
│  Row 2: Execution Pipeline                                       │
│  [6]★→►[7]★→►[8]★→►[9]★→►[10]                                  │
│  Workflow MOP-Test SimCfg Exec-QA Report                         │
│  LLM     JAR      LLM    JAR+QA  Determ.                        │
│                                                                   │
│                  ★ = Milestone Review Pause                       │
├──────────────────────────────────────────────────────────────────┤
│  liteLLM/OpenRouter │ SQLite DB │ Artifacts │ Knowledge Base      │
├──────────────────────────────────────────────────────────────────┤
│  ciq-processor.jar │ cli-automation-standalone.jar │ mockwire.jar │
└──────────────────────────────────────────────────────────────────┘
```

## Pipeline — 10 Steps, 10 Agents

| Step | Agent | Type | Output | Milestone |
|------|-------|------|--------|-----------|
| 1 | Document Parser | LLM | Intermediate Representation | — |
| 2 | UC Analyzer | LLM | Enriched IR + HIL requests | — |
| 3 | Validation Rules Gen | LLM | `<UC>_validation-rules.yaml` | ★ Review |
| 4 | JSON Output Gen | LLM | `<UC>_json-output.yaml` | ★ Review |
| 5 | CIQ Validation QA | JAR + QA | QA report (HTML/Excel) | ★ Review |
| 6 | Workflow Gen | LLM | `<UC>.yaml` (5 phases) | ★ Review |
| 7 | MOP Generation Test | JAR | MOP HTML document | ★ Review |
| 8 | Simulator Config Gen | LLM | MockWire configs + responses | ★ Review |
| 9 | Execution Test QA | JAR + QA | Execution QA report | ★ Review |
| 10 | Final Report | Deterministic | HTML + Excel report | — |

**6 LLM agents** (steps 1-4, 6, 8) with smart retry · **4 deterministic agents** (steps 5, 7, 9, 10)

## Quick Start

### Prerequisites
- Python 3.11+
- Java 8+ (for JAR execution)
- OpenRouter API key ([get one here](https://openrouter.ai/keys))

### Setup
```bash
git clone <repo-url>
cd cr_automation-agentic
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
# source .venv/bin/activate     # Linux/Mac
pip install -e ".[dev]"
```

### Configure
```bash
# API key (choose one)
echo "OPENROUTER_API_KEY=sk-or-v1-your-key" > .env   # .env file
# Or configure via Settings UI after starting

# Place Java JARs in lib/
cp /path/to/ciq-processor-*-cli.jar lib/
cp /path/to/cli-automation-standalone.jar lib/
```

### Run
```bash
./start.sh      # Git Bash
# .\start.ps1   # PowerShell
```
Open http://localhost:8000

### Stop
```bash
./stop.sh       # Git Bash
# .\stop.ps1    # PowerShell
```

## QA Testing Cycle (Steps 5 & 9)

The pipeline includes built-in QA cycles that automatically test generated artifacts:

**Step 5 — CIQ Validation QA:**
- Generates test cases from validation rules (positive + negative scenarios)
- Creates modified CIQ Excel files for each negative test (missing columns, invalid data, pattern violations, cross-sheet violations)
- Executes each test CIQ against `ciq-processor.jar`
- Produces formal QA reports: `test-cases.xlsx` + `qa-execution-report.html/.xlsx`
- Failed test feedback is fed back to the LLM on re-run

**Step 7 — MOP Generation Test:**
- Runs `cli-automation-standalone.jar generate` to produce MOP HTML
- Validates that the workflow YAML + JSON data can generate a proper MOP document

**Step 9 — Execution Test QA:**
- Tests workflow execution against MockWire SSH simulator
- Similar QA cycle to Step 5

### Test Failure Handling
- When QA tests fail, the **Continue** button is hidden
- User must either **Re-run** (regenerate from source step) or **Skip & Continue**
- Re-run mapping: Step 5 → restart from Step 3, Steps 7/9 → restart from Step 6

## Milestone Review

Pipeline pauses after steps 3-9 for user review:
- **View Artifact** — opens fullscreen dialog (HTML reports rendered in iframe, YAML in monospace)
- **Continue** — accept and proceed (hidden if QA tests failed)
- **Skip & Continue** — proceed despite test failures (explicit acknowledgment)
- **Re-run Step** — regenerate with LLM (with previous error/QA feedback)
- **Cancel Pipeline** — stop execution

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

```
data/runs/<run_id>/
├── <UC>_validation-rules.yaml       # Step 3
├── <UC>_json-output.yaml            # Step 4
├── qa/                              # Step 5 QA
│   ├── test-cases.xlsx
│   ├── qa-execution-report.html
│   ├── qa-execution-report.xlsx
│   ├── test_data/TC01_*.xlsx ...
│   └── results/TC01/ TC02/ ...
├── ciq_output/                      # Step 5 JAR output
│   ├── <UC>_VALIDATION_REPORT.json/.html/.xlsx
│   └── <UC>.json
├── <UC>.yaml                        # Step 6 workflow
├── mop_output/*.html                # Step 7 MOP documents
├── simulator/                       # Step 8
│   ├── config/<UC>_SIMULATOR_CONFIG.yaml
│   ├── data/*.txt
│   └── simulator_manifest.yaml
└── <UC>_report.html/.xlsx           # Step 10 final report
```

## LLM Models

Default: `openrouter/google/gemini-2.5-flash` (configurable per-step in Settings)

| Tier | Models | Cost (in/out per 1M tokens) |
|------|--------|----------------------------|
| Best Value | Gemini 2.5 Flash, DeepSeek V3 | $0.075-$0.15 / $0.28-$0.60 |
| Premium | Claude Sonnet 4, Gemini 2.5 Pro, GPT-4o | $1.25-$3 / $10-$15 |
| Free | Gemini 2.5 Flash Free, Llama 3.3 70B | Free (rate limited) |

## Project Structure

```
cr_automation-agentic/
├── src/
│   ├── agents/          # 10 pipeline agents + QA module
│   ├── core/            # Pipeline engine, models, config, events, retry
│   ├── parsers/         # CIQ Excel, MOP, constraints, command output parsers
│   ├── knowledge/       # Few-shot store + embedding similarity
│   ├── storage/         # SQLite DB + artifact file store
│   └── ui/              # NiceGUI app + page sections
├── schemas/             # cr-automation YAML schema references
├── config/              # pipeline_config.yaml, models.yaml
├── lib/                 # Java JARs (not committed)
├── data/                # Runtime data (not committed)
└── docs/                # Architecture documentation
```

## Key Features

- **10-Step Pipeline** — comprehensive CIQ-to-execution automation
- **Built-in QA Cycles** — automatic test case generation, execution, and reporting
- **Milestone Review** — pause after each generation step for human review
- **QA Feedback Loop** — failed test cases fed back to LLM for self-correction
- **Human-in-the-Loop** — pipeline pauses for ambiguous fields
- **Smart Retry** — LLM steps retry with error context (column name mismatches, YAML syntax)
- **Resume After Restart** — orphaned milestones resume from correct step
- **Few-shot Learning** — successful configs stored as examples for future runs
- **Real-time Progress** — two-row step tracker with arrow connectors, WebSocket updates
- **Cascading Filters** — filter by node type and activity across all sections

## Related Projects

| Project | Purpose |
|---------|---------|
| [ciq-processor](https://github.com/anautiyal83/ciq-processor) | CIQ Excel validation + JSON generation |
| [cli-automation-standalone](https://github.com/anautiyal83/cli-automation-standalone) | MOP generation + workflow execution |
| [mockwire-simulator](https://github.com/anautiyal83/mockwire) | SSH mock simulator for testing |
| [sbc-python-scripts](https://github.com/anautiyal83/sbc-python-scripts) | NETCONF XML generation/rollback |
