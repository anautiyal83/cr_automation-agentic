"""Document upload validation service per FR-002."""
from __future__ import annotations

from pathlib import Path
from uuid import UUID

from src.core.models import (
    UCDocumentSet,
    ValidationError,
    ValidationResult,
)
from src.parsers.ciq_parser import parse_ciq
from src.parsers.mop_parser import parse_mop
from src.parsers.command_output_parser import parse_command_outputs


async def validate_documents(
    uc_name: str,
    ciq_path: str,
    mop_path: str,
    constraints_path: str | None,
    command_outputs_path: str,
    mandatory_columns: list[str] | None = None,
) -> ValidationResult:
    """Validate uploaded documents — checks file existence, format, and basic parseability.

    Column-level and content-level analysis is deferred to the LLM agents (steps 1-2).
    """
    errors: list[ValidationError] = []

    # Validate CIQ Excel — format only
    if not Path(ciq_path).exists():
        errors.append(ValidationError(
            document="ciq", error_type="missing_file",
            message="CIQ Excel file not found",
        ))
    else:
        suffix = Path(ciq_path).suffix.lower()
        if suffix not in (".xlsx", ".xls"):
            errors.append(ValidationError(
                document="ciq", error_type="invalid_format",
                message=f"CIQ must be .xlsx or .xls, got {suffix}",
            ))
        else:
            try:
                result = parse_ciq(ciq_path, mandatory_columns=[])
                if not result["rows"] and not result["headers"]:
                    errors.append(ValidationError(
                        document="ciq", error_type="empty_content",
                        message="CIQ file contains no readable data",
                    ))
            except Exception as exc:
                errors.append(ValidationError(
                    document="ciq", error_type="parse_error",
                    message=f"Failed to parse CIQ: {exc}",
                ))

    # Validate MOP — format only
    if not Path(mop_path).exists():
        errors.append(ValidationError(
            document="mop", error_type="missing_file",
            message="MOP document not found",
        ))
    else:
        suffix = Path(mop_path).suffix.lower()
        if suffix not in (".docx", ".pdf", ".txt"):
            errors.append(ValidationError(
                document="mop", error_type="invalid_format",
                message=f"MOP must be .docx, .pdf, or .txt, got {suffix}",
            ))

    # Validate constraints (optional) — format only
    if constraints_path and Path(constraints_path).exists():
        suffix = Path(constraints_path).suffix.lower()
        if suffix not in (".txt", ".yaml", ".yml", ".json", ".csv"):
            errors.append(ValidationError(
                document="constraints", error_type="invalid_format",
                message=f"Constraints file format not supported: {suffix}",
            ))

    # Validate command outputs — existence only
    if not Path(command_outputs_path).exists():
        errors.append(ValidationError(
            document="command_outputs", error_type="missing_file",
            message="Command outputs file not found",
        ))

    return ValidationResult(valid=len(errors) == 0, errors=errors)
