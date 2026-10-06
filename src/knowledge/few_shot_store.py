"""Few-shot knowledge base — stores and retrieves successful UC configs."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from uuid import UUID

from src.core.models import KnowledgeBaseEntry


class FewShotStore:
    def __init__(self, base_dir: str = "./data/knowledge") -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.index_path = self.base_dir / "index.json"
        self._index: dict[str, dict] = self._load_index()

    def _load_index(self) -> dict[str, dict]:
        if self.index_path.exists():
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        return {}

    def _save_index(self) -> None:
        self.index_path.write_text(
            json.dumps(self._index, indent=2, default=str), encoding="utf-8"
        )

    async def add_entry(
        self,
        uc_name: str,
        uc_description: str,
        configs: dict[str, str],  # config_type -> source file path
        source_run_id: UUID,
        embedding: list[float] | None = None,
    ) -> KnowledgeBaseEntry:
        """Add or replace a knowledge base entry for a UC. Latest-only per UC name."""
        # Remove old entry if exists
        uc_dir = self.base_dir / self._safe_name(uc_name)
        if uc_dir.exists():
            shutil.rmtree(uc_dir)
        uc_dir.mkdir(parents=True)

        # Copy config files
        stored_paths = {}
        for config_type, src_path in configs.items():
            src = Path(src_path)
            if src.exists():
                dest = uc_dir / src.name
                shutil.copy2(src, dest)
                stored_paths[config_type] = str(dest)

        entry = KnowledgeBaseEntry(
            uc_name=uc_name,
            uc_description=uc_description,
            embedding=embedding or [],
            validation_rules_path=stored_paths.get("validation_rules", ""),
            json_template_path=stored_paths.get("json_template", ""),
            workflow_path=stored_paths.get("workflow", ""),
            scripts_paths=[stored_paths[k] for k in stored_paths if k == "python_script"],
            source_run_id=source_run_id,
        )

        # Save metadata
        meta_path = uc_dir / "metadata.json"
        meta_path.write_text(entry.model_dump_json(indent=2), encoding="utf-8")

        # Update index (latest only)
        self._index[uc_name] = {
            "dir": str(uc_dir),
            "description": uc_description,
            "source_run_id": str(source_run_id),
        }
        self._save_index()

        return entry

    async def get_entry(self, uc_name: str) -> KnowledgeBaseEntry | None:
        if uc_name not in self._index:
            return None
        uc_dir = Path(self._index[uc_name]["dir"])
        meta_path = uc_dir / "metadata.json"
        if not meta_path.exists():
            return None
        return KnowledgeBaseEntry.model_validate_json(meta_path.read_text(encoding="utf-8"))

    async def list_entries(self) -> list[KnowledgeBaseEntry]:
        entries = []
        for uc_name in self._index:
            entry = await self.get_entry(uc_name)
            if entry:
                entries.append(entry)
        return entries

    async def get_all_descriptions(self) -> list[tuple[str, str]]:
        """Return (uc_name, description) pairs for similarity matching."""
        return [
            (name, info.get("description", ""))
            for name, info in self._index.items()
        ]

    def _safe_name(self, name: str) -> str:
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)
