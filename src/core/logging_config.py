"""Structured JSON audit logging for pipeline executions per FR-015, FR-042."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path


class JSONFormatter(logging.Formatter):
    """Structured JSON log formatter that masks sensitive data."""

    SENSITIVE_KEYS = {"api_key", "openrouter_api_key", "authorization", "token"}

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[1]:
            log_entry["exception"] = str(record.exc_info[1])
        if hasattr(record, "run_id"):
            log_entry["run_id"] = str(record.run_id)
        if hasattr(record, "step_number"):
            log_entry["step_number"] = record.step_number

        # Mask sensitive data
        msg = log_entry["message"]
        for key in self.SENSITIVE_KEYS:
            if key in msg.lower():
                log_entry["message"] = "[SENSITIVE DATA MASKED]"
                break

        return json.dumps(log_entry, default=str)


def setup_logging(log_dir: str = "./data/logs", level: str = "INFO") -> None:
    """Configure structured logging with file and console handlers."""
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # File handler — JSON structured logs
    log_file = Path(log_dir) / "pipeline.log"
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setFormatter(JSONFormatter())
    root.addHandler(file_handler)

    # Console handler — human readable
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    root.addHandler(console_handler)
