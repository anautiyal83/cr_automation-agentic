"""Command output parser — extracts command-response pairs from CLI/API session logs."""
from __future__ import annotations

import re
from pathlib import Path


def parse_command_outputs(file_path: str | Path) -> dict:
    """Parse command output documents into structured command-response pairs.

    Returns dict with: commands, errors
    """
    text = Path(file_path).read_text(encoding="utf-8")
    commands = _extract_command_pairs(text)

    return {
        "commands": commands,
        "errors": [] if commands else ["No command-response pairs found"],
    }


def _extract_command_pairs(text: str) -> list[dict]:
    """Extract command-response pairs from CLI session log."""
    pairs = []
    lines = text.split("\n")

    current_command = None
    response_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if _is_command_prompt(stripped):
            # Save previous pair
            if current_command is not None:
                response = "\n".join(response_lines).strip()
                if response:
                    pairs.append(_build_pair(current_command, response))

            current_command = _extract_command_from_prompt(stripped)
            response_lines = []
        elif current_command is not None:
            if not _is_noise(stripped):
                response_lines.append(stripped)

    # Save last pair
    if current_command is not None:
        response = "\n".join(response_lines).strip()
        if response:
            pairs.append(_build_pair(current_command, response))

    return pairs


def _is_command_prompt(line: str) -> bool:
    """Detect if a line is a CLI command prompt."""
    prompt_patterns = [
        r'^[\w@\-\.]+[#>$]\s',     # user@host# or user@host>
        r'^[\w\(\)\-]+#\s',         # router(config)#
        r'^[\w\(\)\-]+>\s',         # router>
    ]
    return any(re.match(p, line) for p in prompt_patterns)


def _extract_command_from_prompt(line: str) -> str:
    """Remove the prompt prefix to get the actual command."""
    match = re.match(r'^[\w@\-\.\(\)]+[#>$]\s*(.*)', line)
    return match.group(1).strip() if match else line.strip()


def _is_noise(line: str) -> bool:
    """Filter out prompts, banners, and noise."""
    noise_patterns = [
        r'^[-=*]{3,}$',             # separator lines
        r'^Welcome to',              # welcome banners
        r'^Last login:',             # login info
        r'^\s*$',                    # blank
    ]
    return any(re.match(p, line, re.IGNORECASE) for p in noise_patterns)


def _build_pair(command: str, response: str) -> dict:
    """Build a command-response pair with extracted variables and pattern."""
    variables = re.findall(r'\{(\w+)\}', command)

    # Build response pattern: escape special regex chars, keep {var} as named groups
    pattern = re.escape(response)
    for var in re.findall(r'\{(\w+)\}', response):
        pattern = pattern.replace(re.escape(f"{{{var}}}"), f"(?P<{var}>.+?)")

    return {
        "command": command,
        "expected_response": response,
        "response_pattern": pattern,
        "variables_in_response": re.findall(r'\{(\w+)\}', response),
    }
