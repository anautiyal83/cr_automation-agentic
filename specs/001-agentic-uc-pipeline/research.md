# Research: Agentic UC Pipeline

**Date**: 2026-10-06 | **Plan**: [plan.md](plan.md)

## R1: Google ADK Multi-Agent Orchestration

**Decision**: Use Google ADK's `Agent` and `SequentialAgent`/`ParallelAgent` primitives to define 9 dedicated agents orchestrated by a parent pipeline controller.

**Rationale**: ADK natively supports multi-agent hierarchies with tool-use and structured output. Each agent gets its own system prompt and tool set, enabling independent development, testing, and prompt tuning. The parent controller manages step sequencing, state passing, and error handling.

**Alternatives considered**:
- LangChain/LangGraph: More mature ecosystem but heavier, and user mandated Google ADK.
- CrewAI: Good multi-agent but less structured output control than ADK.
- Custom orchestration without ADK: More control but loses ADK's built-in tool-use, session management, and structured output enforcement.

**Key implementation notes**:
- ADK agents define tools via Python functions decorated with `@tool`.
- Structured output uses ADK's `output_schema` parameter with Pydantic models.
- liteLLM integration: ADK supports custom model backends; configure liteLLM as the model provider to route through OpenRouter.
- Each agent receives the intermediate representation as input context, not raw documents.

## R2: liteLLM + OpenRouter Integration with Google ADK

**Decision**: Use liteLLM as the LLM backend for Google ADK agents, routing all calls through OpenRouter for model flexibility.

**Rationale**: liteLLM provides a unified API across 100+ LLM providers via OpenRouter. Per-step model configuration (FR-038) is achieved by setting different model IDs in each agent's config. This decouples agent logic from model choice.

**Alternatives considered**:
- Direct Google Gemini API: Limited to Google models only; no flexibility for Claude, GPT-4, etc.
- Direct OpenRouter API: Works but loses liteLLM's unified interface, retry logic, and model aliasing.

**Key implementation notes**:
- liteLLM's `completion()` function accepts `model="openrouter/<provider>/<model>"` format.
- ADK's model backend can be overridden to use liteLLM via a custom `ModelClient`.
- API keys stored in config, loaded from `config/pipeline_config.yaml` or UI settings (FR-042 — masked, not logged).
- Infrastructure retry (FR-020) implemented at liteLLM level with exponential backoff.

## R3: NiceGUI Single-Page App Architecture

**Decision**: Build the UI as a single NiceGUI application with collapsible `ui.expansion` panels for each section, a persistent sticky header for pipeline progress, and WebSocket-driven real-time updates.

**Rationale**: NiceGUI's Quasar-based components (expansion panels, tables, dialogs) natively support all UI requirements without CSS hacks. WebSocket binding means state changes propagate to the browser instantly.

**Alternatives considered**:
- Streamlit: Rerun-on-interaction model makes real-time updates and sticky banners difficult.
- Reflex: Good alternative but smaller community and less battle-tested.
- FastAPI + React: Best UX but requires dual codebase and JavaScript skills.

**Key implementation notes**:
- `ui.header(fixed=True)` for sticky progress banner.
- `ui.expansion()` for collapsible sections (Upload, Progress, History, Cleanup, Settings).
- `ui.table()` with column filtering for run history (FR-024).
- `ui.upload()` for document uploads with validation callbacks.
- `ui.dialog()` for HIL inline response forms.
- Dark mode via `ui.dark_mode(True)` with toggle button (FR-041).
- Pipeline runs execute in background tasks (`asyncio`) — UI remains responsive.

## R4: Pipeline State Persistence with SQLite

**Decision**: Use SQLite for pipeline run metadata persistence and file system for artifact storage.

**Rationale**: SQLite is zero-configuration, file-based, and sufficient for single-user deployment (assumption). Pipeline run metadata (status, timestamps, step results, retry histories) maps well to relational tables. Artifacts (YAML configs, reports, logs) are stored as files referenced by the database.

**Alternatives considered**:
- TinyDB/JSON files: Simpler but no query capability for filtered history (FR-024).
- PostgreSQL: Overkill for single-user; adds deployment complexity.
- SQLAlchemy ORM: Considered for abstraction but adds unnecessary complexity for a simple schema (YAGNI — Principle VII).

**Key implementation notes**:
- Tables: `pipeline_runs`, `step_results`, `retry_attempts`, `hil_requests`, `settings`.
- Artifacts stored under `data/runs/<run_id>/` with subdirectories per step.
- Run cancellation (FR-019) updates status to `cancelled` and preserves completed step artifacts.
- Cleanup (FR-022) deletes artifact files and removes DB records.

## R5: Invoking Java Modules as External Processes

**Decision**: Invoke ciq-processor and cli-automation-standalone as Java subprocess calls from Python using `subprocess.run()` with JSON/text I/O.

**Rationale**: Constitution Principle VI mandates no modification to core Java modules. Subprocess invocation is the cleanest integration — Python calls the JAR, passes input via stdin/args, and captures stdout/stderr. This maintains the layered architecture boundary.

**Alternatives considered**:
- Py4J / JPype (JNI bridge): Tighter integration but couples Python to JVM lifecycle, harder to debug, and violates the spirit of "invoke via existing API/CLI interface."
- REST wrapper around JARs: Adds unnecessary network hop for single-machine deployment.
- Rewrite in Python: Violates Principle VI (no modification/fork of core modules).

**Key implementation notes**:
- ciq-processor: `java -jar ciq-processor.jar --input <ciq.xlsx> --rules <validation_rules.yaml> --output <result.json>`
- cli-automation-standalone: `java -jar cli-automation-standalone.jar --workflow <workflow.yaml> --simulator-config <sim_config.yaml> --output <result.json>`
- Parse JSON output from both JARs for test result extraction.
- JRE availability is a prerequisite; documented in quickstart.md.
- Subprocess timeouts configured per pipeline config.

## R6: Few-Shot Knowledge Base and Similarity Matching

**Decision**: Store successful UC configs in a file-based knowledge base with metadata. Use sentence-transformers embedding similarity for retrieval.

**Rationale**: As UCs accumulate, the system needs to find similar past configs to use as few-shot examples (FR-036). Embedding-based similarity captures semantic UC type relationships (e.g., "VoLTE provisioning" similar to "VoNR provisioning") better than keyword matching.

**Alternatives considered**:
- Keyword/tag-based matching: Simple but misses semantic similarity.
- Vector database (ChromaDB, Pinecone): More capable but overkill for hundreds of UCs (YAGNI).
- No few-shot: Degrades generation quality as proven by testing clarification.

**Key implementation notes**:
- Knowledge base stored under `data/knowledge/` with per-UC directories.
- Each entry: UC metadata JSON + the 3 successful YAML configs + optional scripts.
- Embedding model: `all-MiniLM-L6-v2` (lightweight, fast, good for short text similarity).
- On new UC: embed UC description → cosine similarity against stored embeddings → top-K (e.g., 3) most similar → include in agent prompts as few-shot examples.
- Auto-add to knowledge base on successful pipeline completion (FR-036).

## R7: Document Chunking Strategy for Large CIQ Files

**Decision**: Use row-based chunking for CIQ Excel (N rows per chunk) and section-based chunking for MOP/constraints documents.

**Rationale**: CIQ files can have hundreds/thousands of rows. Row-based chunking preserves data integrity (each chunk is a valid subset of records). MOP documents have natural section boundaries (procedures, steps) that serve as chunk points.

**Alternatives considered**:
- Token-based chunking: Risk splitting mid-record or mid-command.
- No chunking (large context model only): Unreliable — model availability and cost vary.

**Key implementation notes**:
- CIQ: Parse headers once, then process N rows at a time (N configurable, default 100).
- MOP: Split by procedure/section headings; each chunk is a complete procedure.
- Constraints: Split by rule blocks (structured tables) or paragraph boundaries (free-form).
- Aggregation: Merge intermediate representations from all chunks into unified structure.
- Validate no records lost: count input rows vs. output records.

## R8: HTML and Excel Report Generation

**Decision**: Use Jinja2 templates for HTML reports and openpyxl/xlsxwriter for Excel reports.

**Rationale**: Jinja2 allows rich, interactive HTML with expandable sections and syntax highlighting for YAML/JSON diffs. openpyxl produces structured Excel with formatting suitable for QA sign-off.

**Alternatives considered**:
- Pandas to_html: Limited interactivity.
- ReportLab: PDF-focused, not HTML.
- Allure/pytest-html: Test framework specific; we need custom report content.

**Key implementation notes**:
- HTML report: Collapsible per-step sections, syntax-highlighted YAML diffs, color-coded pass/fail, embedded agent prompt/response logs, regression diff section.
- Excel report: Summary sheet (overall pass/fail, UC metadata, timestamps), per-step sheet (step name, status, error summary), designed for print/sign-off.
- Both generated by `report_generator.py` (pipeline step 9) from test execution results.

## R9: Human-in-the-Loop (HIL) Implementation

**Decision**: Implement HIL as an async event-driven pause using NiceGUI dialogs and Python asyncio events.

**Rationale**: When the pipeline needs human input, it publishes a HIL request to the UI and awaits an asyncio.Event. The NiceGUI dialog presents the question and sets the event with the user's response. Pipeline resumes immediately. This keeps the pipeline server-side while the UI handles interaction.

**Alternatives considered**:
- Polling-based: Pipeline polls DB for user response — adds latency.
- Separate HIL queue service: Over-engineered for single-user (YAGNI).

**Key implementation notes**:
- HIL request: `{field, document_section, agent_inference, question, options}`.
- UI renders a dialog with the agent's inference and input field/dropdown for user response.
- Pipeline state preserved in memory (asyncio task suspended at await point).
- If user closes browser, HIL request persists in DB; pipeline waits until user returns and responds.
- Multiple HIL requests can be batched (all ambiguities from steps 1-2 presented together).
