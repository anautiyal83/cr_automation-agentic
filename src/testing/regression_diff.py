"""Config regression diff — compares new configs against last successful run per FR-040."""
from __future__ import annotations

from pathlib import Path

from ruamel.yaml import YAML


def compute_diff(new_path: str | Path, old_path: str | Path) -> dict:
    """Compute YAML-level diff between new and old config files.

    Returns dict with: changed (bool), additions, removals, modifications, summary
    """
    yaml = YAML()
    try:
        new_doc = yaml.load(Path(new_path))
        old_doc = yaml.load(Path(old_path))
    except Exception as e:
        return {"changed": True, "error": str(e), "summary": f"Could not parse: {e}"}

    additions, removals, modifications = _deep_diff(old_doc, new_doc)

    changed = bool(additions or removals or modifications)
    summary = "No changes detected." if not changed else (
        f"{len(additions)} additions, {len(removals)} removals, {len(modifications)} modifications"
    )

    return {
        "changed": changed,
        "additions": additions,
        "removals": removals,
        "modifications": modifications,
        "summary": summary,
    }


def compute_regression_report(
    new_configs: dict[str, str | Path],
    old_configs: dict[str, str | Path],
) -> dict:
    """Compare all config types between new and old runs.

    Args:
        new_configs: {config_type: file_path} for new run
        old_configs: {config_type: file_path} for last successful run

    Returns dict with per-config diffs and overall summary
    """
    diffs = {}
    any_changed = False

    for config_type in new_configs:
        new_path = new_configs[config_type]
        old_path = old_configs.get(config_type)

        if old_path and Path(old_path).exists() and Path(new_path).exists():
            diff = compute_diff(new_path, old_path)
            diffs[config_type] = diff
            if diff.get("changed"):
                any_changed = True
        else:
            diffs[config_type] = {
                "changed": True,
                "summary": "No previous version to compare",
            }
            any_changed = True

    return {
        "any_changed": any_changed,
        "diffs": diffs,
    }


def _deep_diff(old: dict | list | None, new: dict | list | None,
               path: str = "") -> tuple[list, list, list]:
    """Recursively diff two YAML structures."""
    additions: list[dict] = []
    removals: list[dict] = []
    modifications: list[dict] = []

    if old is None and new is None:
        return additions, removals, modifications
    if old is None:
        additions.append({"path": path, "value": new})
        return additions, removals, modifications
    if new is None:
        removals.append({"path": path, "value": old})
        return additions, removals, modifications

    if isinstance(old, dict) and isinstance(new, dict):
        all_keys = set(old.keys()) | set(new.keys())
        for key in sorted(all_keys):
            child_path = f"{path}.{key}" if path else str(key)
            if key not in old:
                additions.append({"path": child_path, "value": new[key]})
            elif key not in new:
                removals.append({"path": child_path, "value": old[key]})
            else:
                a, r, m = _deep_diff(old[key], new[key], child_path)
                additions.extend(a)
                removals.extend(r)
                modifications.extend(m)
    elif isinstance(old, list) and isinstance(new, list):
        for i in range(max(len(old), len(new))):
            child_path = f"{path}[{i}]"
            if i >= len(old):
                additions.append({"path": child_path, "value": new[i]})
            elif i >= len(new):
                removals.append({"path": child_path, "value": old[i]})
            else:
                a, r, m = _deep_diff(old[i], new[i], child_path)
                additions.extend(a)
                removals.extend(r)
                modifications.extend(m)
    elif old != new:
        modifications.append({"path": path, "old": old, "new": new})

    return additions, removals, modifications
