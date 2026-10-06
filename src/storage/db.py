"""SQLite database layer for pipeline run persistence."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import aiosqlite

_DEFAULT_DB = "./data/pipeline.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    id TEXT PRIMARY KEY,
    document_set_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    current_step INTEGER NOT NULL DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    duration_seconds REAL,
    config_snapshot TEXT NOT NULL DEFAULT '{}',
    intermediate_repr TEXT,
    error_summary TEXT,
    is_regression_run INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS uc_document_sets (
    id TEXT PRIMARY KEY,
    uc_name TEXT NOT NULL,
    ciq_file_path TEXT NOT NULL,
    mop_file_path TEXT NOT NULL,
    constraints_file_path TEXT,
    command_outputs_path TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    validated INTEGER NOT NULL DEFAULT 0,
    validation_errors TEXT,
    selected_sheets TEXT
);

CREATE TABLE IF NOT EXISTS step_results (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(id),
    step_number INTEGER NOT NULL,
    step_name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    started_at TEXT,
    completed_at TEXT,
    input_summary TEXT,
    output_summary TEXT,
    output_artifact_paths TEXT,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    agent_model TEXT
);

CREATE TABLE IF NOT EXISTS retry_attempts (
    id TEXT PRIMARY KEY,
    step_result_id TEXT NOT NULL REFERENCES step_results(id),
    attempt_number INTEGER NOT NULL,
    retry_type TEXT NOT NULL,
    error_context TEXT NOT NULL,
    agent_prompt TEXT,
    agent_response TEXT,
    outcome TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS hil_requests (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(id),
    step_number INTEGER NOT NULL,
    field_or_section TEXT NOT NULL,
    agent_inference TEXT NOT NULL,
    question TEXT NOT NULL,
    options TEXT,
    user_response TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    answered_at TEXT
);

CREATE TABLE IF NOT EXISTS generated_configs (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES pipeline_runs(id),
    config_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    schema_valid INTEGER,
    version INTEGER NOT NULL DEFAULT 1,
    generated_by_step INTEGER NOT NULL,
    content_hash TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS test_reports (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL UNIQUE REFERENCES pipeline_runs(id),
    html_report_path TEXT NOT NULL,
    excel_report_path TEXT NOT NULL,
    total_tests INTEGER NOT NULL DEFAULT 0,
    passed_tests INTEGER NOT NULL DEFAULT 0,
    failed_tests INTEGER NOT NULL DEFAULT 0,
    has_regression_diff INTEGER NOT NULL DEFAULT 0,
    generated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS knowledge_base_entries (
    id TEXT PRIMARY KEY,
    uc_name TEXT NOT NULL,
    uc_description TEXT NOT NULL DEFAULT '',
    embedding BLOB,
    validation_rules_path TEXT NOT NULL,
    json_template_path TEXT NOT NULL,
    workflow_path TEXT NOT NULL,
    scripts_paths TEXT,
    source_run_id TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status ON pipeline_runs(status);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_doc_set ON pipeline_runs(document_set_id);
CREATE INDEX IF NOT EXISTS idx_step_results_run ON step_results(run_id);
CREATE INDEX IF NOT EXISTS idx_retry_attempts_step ON retry_attempts(step_result_id);
CREATE INDEX IF NOT EXISTS idx_hil_requests_run ON hil_requests(run_id);
CREATE INDEX IF NOT EXISTS idx_generated_configs_run ON generated_configs(run_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_base_uc ON knowledge_base_entries(uc_name);
"""


class Database:
    def __init__(self, db_path: str = _DEFAULT_DB) -> None:
        self.db_path = db_path

    async def initialize(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(SCHEMA_SQL)
            await db.commit()

    async def execute(self, sql: str, params: tuple = ()) -> list[dict]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(sql, params)
            rows = await cursor.fetchall()
            await db.commit()
            return [dict(row) for row in rows]

    async def execute_insert(self, sql: str, params: tuple = ()) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(sql, params)
            await db.commit()

    async def insert_model(self, table: str, data: dict) -> None:
        columns = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        values = []
        for v in data.values():
            if isinstance(v, (dict, list)):
                values.append(json.dumps(v))
            elif isinstance(v, bool):
                values.append(int(v))
            else:
                values.append(str(v) if v is not None else None)
        sql = f"INSERT OR REPLACE INTO {table} ({columns}) VALUES ({placeholders})"
        await self.execute_insert(sql, tuple(values))

    async def get_by_id(self, table: str, row_id: str) -> dict | None:
        rows = await self.execute(
            f"SELECT * FROM {table} WHERE id = ?", (row_id,)
        )
        return rows[0] if rows else None

    async def update(self, table: str, row_id: str, updates: dict) -> None:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = []
        for v in updates.values():
            if isinstance(v, (dict, list)):
                values.append(json.dumps(v))
            elif isinstance(v, bool):
                values.append(int(v))
            else:
                values.append(str(v) if v is not None else None)
        values.append(row_id)
        await self.execute_insert(
            f"UPDATE {table} SET {set_clause} WHERE id = ?", tuple(values)
        )

    async def delete(self, table: str, row_id: str) -> None:
        await self.execute_insert(f"DELETE FROM {table} WHERE id = ?", (row_id,))

    async def query(self, table: str, where: str = "", params: tuple = (),
                    order_by: str = "", limit: int = 0) -> list[dict]:
        sql = f"SELECT * FROM {table}"
        if where:
            sql += f" WHERE {where}"
        if order_by:
            sql += f" ORDER BY {order_by}"
        if limit:
            sql += f" LIMIT {limit}"
        return await self.execute(sql, params)


async def init_database(db_path: str = _DEFAULT_DB) -> Database:
    db = Database(db_path)
    await db.initialize()
    return db


if __name__ == "__main__":
    import asyncio

    path = _DEFAULT_DB
    if "--path" in sys.argv:
        idx = sys.argv.index("--path")
        path = sys.argv[idx + 1]

    async def _init():
        db = await init_database(path)
        print(f"Database initialized at {path}")

    asyncio.run(_init())
