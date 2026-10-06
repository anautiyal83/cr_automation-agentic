"""MOP document parser — extracts CLI/REST commands and execution steps."""
from __future__ import annotations

import re
from pathlib import Path


def parse_mop(file_path: str | Path) -> dict:
    """Parse a MOP document and extract commands, steps, and expected outputs.

    Returns dict with: mop_type, steps, raw_text, errors, hil_needed_sections
    """
    path = Path(file_path)
    suffix = path.suffix.lower()

    if suffix == ".txt":
        raw_text = path.read_text(encoding="utf-8")
    elif suffix == ".docx":
        raw_text = _parse_docx(path)
    elif suffix == ".pdf":
        raw_text = _parse_pdf(path)
    else:
        return {"mop_type": "cli", "steps": [], "raw_text": "",
                "errors": [f"Unsupported format: {suffix}"], "hil_needed_sections": []}

    steps = _extract_steps(raw_text)
    mop_type = _detect_mop_type(steps)
    hil_sections = _identify_ambiguous_sections(raw_text, steps)

    return {
        "mop_type": mop_type,
        "steps": steps,
        "raw_text": raw_text,
        "errors": [],
        "hil_needed_sections": hil_sections,
    }


def _parse_docx(path: Path) -> str:
    from docx import Document
    doc = Document(str(path))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    return "\n".join(paragraphs)


def _parse_pdf(path: Path) -> str:
    from PyPDF2 import PdfReader
    reader = PdfReader(str(path))
    text_parts = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            text_parts.append(text)
    return "\n".join(text_parts)


def _extract_steps(text: str) -> list[dict]:
    """Extract numbered steps with commands and expected outputs."""
    steps = []
    # Match patterns like "Step 1:", "1.", "1)", "Step 1 -"
    step_pattern = re.compile(
        r'(?:Step\s+)?(\d+)[.):]\s*[-–]?\s*(.*?)(?=(?:Step\s+)?\d+[.):]\s*[-–]?|\Z)',
        re.DOTALL | re.IGNORECASE
    )

    matches = step_pattern.findall(text)

    for step_num, content in matches:
        content = content.strip()
        if not content:
            continue

        step = {
            "step_number": int(step_num),
            "description": "",
            "command_type": "cli_command",
            "command_template": "",
            "expected_output_pattern": "",
            "variables": [],
        }

        lines = content.split("\n")
        desc_lines = []
        cmd_lines = []
        output_lines = []
        in_output = False

        for line in lines:
            line_stripped = line.strip()
            if not line_stripped:
                continue

            if _is_cli_command(line_stripped):
                cmd_lines.append(line_stripped)
                step["command_type"] = "cli_command"
            elif _is_rest_call(line_stripped):
                cmd_lines.append(line_stripped)
                step["command_type"] = "rest_api_call"
            elif any(kw in line_stripped.lower() for kw in ["expected output", "expected response", "output:"]):
                in_output = True
            elif in_output:
                output_lines.append(line_stripped)
            else:
                desc_lines.append(line_stripped)

        step["description"] = " ".join(desc_lines)
        step["command_template"] = "\n".join(cmd_lines)
        step["expected_output_pattern"] = "\n".join(output_lines)

        # Extract {variables}
        variables = re.findall(r'\{(\w+)\}', step["command_template"])
        step["variables"] = [{"name": v, "source_field": v} for v in variables]

        if step["command_template"] or step["description"]:
            steps.append(step)

    return steps


def _is_cli_command(line: str) -> bool:
    cli_indicators = [
        "router", "switch", "admin@", "#", ">", "show ", "configure ",
        "interface ", "ip address", "no shutdown", "vlan ", "commit",
        "display ", "set ", "delete ", "run ", "exec ",
    ]
    lower = line.lower().strip()
    return any(lower.startswith(ind) or ind in lower for ind in cli_indicators)


def _is_rest_call(line: str) -> bool:
    rest_indicators = ["GET ", "POST ", "PUT ", "DELETE ", "PATCH ", "http://", "https://", "curl "]
    return any(line.strip().upper().startswith(ind) or ind in line for ind in rest_indicators)


def _detect_mop_type(steps: list[dict]) -> str:
    types = {s["command_type"] for s in steps}
    if "cli_command" in types and "rest_api_call" in types:
        return "mixed"
    if "rest_api_call" in types:
        return "rest"
    return "cli"


def _identify_ambiguous_sections(text: str, steps: list[dict]) -> list[dict]:
    """Identify sections where command type is unclear — need HIL."""
    ambiguous = []
    for step in steps:
        if not step["command_template"] and step["description"]:
            ambiguous.append({
                "step_number": step["step_number"],
                "description": step["description"],
                "reason": "No command template extracted — unclear if CLI or REST",
            })
    return ambiguous


def chunk_mop_text(text: str, max_chars: int = 8000) -> list[str]:
    """Split MOP text into section-based chunks."""
    sections = re.split(r'\n(?=(?:Step\s+)?\d+[.):]\s)', text)
    chunks = []
    current = ""
    for section in sections:
        if len(current) + len(section) > max_chars and current:
            chunks.append(current)
            current = section
        else:
            current += ("\n" if current else "") + section
    if current:
        chunks.append(current)
    return chunks
