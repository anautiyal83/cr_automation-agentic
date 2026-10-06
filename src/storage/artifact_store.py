"""File-based artifact storage for pipeline runs."""
from __future__ import annotations

import hashlib
import shutil
import zipfile
from pathlib import Path
from uuid import UUID


class ArtifactStore:
    def __init__(self, base_directory: str = "./data/runs") -> None:
        self.base_dir = Path(base_directory)
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: UUID) -> Path:
        d = self.base_dir / str(run_id)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def step_dir(self, run_id: UUID, step_number: int) -> Path:
        d = self.run_dir(run_id) / f"step_{step_number}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def save_artifact(self, run_id: UUID, step_number: int,
                      filename: str, content: str | bytes) -> Path:
        d = self.step_dir(run_id, step_number)
        path = d / filename
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content, encoding="utf-8")
        return path

    def read_artifact(self, path: str | Path) -> str:
        return Path(path).read_text(encoding="utf-8")

    def content_hash(self, path: str | Path) -> str:
        content = Path(path).read_bytes()
        return hashlib.sha256(content).hexdigest()

    def list_artifacts(self, run_id: UUID) -> list[Path]:
        run_path = self.base_dir / str(run_id)
        if not run_path.exists():
            return []
        return sorted(run_path.rglob("*") if run_path.is_dir() else [])

    def delete_artifact(self, path: str | Path) -> None:
        p = Path(path)
        if p.is_file():
            p.unlink()
        elif p.is_dir():
            shutil.rmtree(p)

    def delete_run(self, run_id: UUID) -> None:
        run_path = self.base_dir / str(run_id)
        if run_path.exists():
            shutil.rmtree(run_path)

    def package_artifacts(self, run_id: UUID) -> Path:
        """Create a zip package of all artifacts for a pipeline run."""
        run_path = self.run_dir(run_id)
        zip_path = run_path / f"artifacts_{run_id}.zip"

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for file_path in run_path.rglob("*"):
                if file_path == zip_path:
                    continue
                if file_path.is_file():
                    arcname = file_path.relative_to(run_path)
                    zf.write(file_path, arcname)

        return zip_path

    def get_latest_successful_configs(self, uc_name: str,
                                       configs_metadata: list[dict]) -> dict[str, Path] | None:
        """Find latest successful configs for a UC name for regression diff.

        configs_metadata should be a list of dicts with 'config_type' and 'file_path' keys,
        fetched from the DB for the most recent successful run of this UC.
        """
        if not configs_metadata:
            return None
        result = {}
        for cfg in configs_metadata:
            path = Path(cfg["file_path"])
            if path.exists():
                result[cfg["config_type"]] = path
        return result if result else None
