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


DEFAULT_MANDATORY_COLUMNS = ["Hostname", "IP_Address"]


async def validate_documents(
    uc_name: str,
    ciq_path: str,
    mop_path: str,
    constraints_path: str | None,
    command_outputs_path: str,
    mandatory_columns: list[str] | None = None,
) -> ValidationResult:
    """Validate uploaded documents for format correctness and minimum content."""
    errors: list[ValidationError] = []
    mandatory = mandatory_columns or DEFAULT_MANDATORY_COLUMNS

    # Validate CIQ Excel
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
            result = parse_ciq(ciq_path, mandatory_columns=mandatory)
            for err in result["errors"]:
                if "Missing mandatory" in err:
                    errors.append(ValidationError(
                        document="ciq", error_type="missing_column",
                        message=err,
                    ))
                else:
                    errors.append(ValidationError(
                        document="ciq", error_type="parse_error",
                        message=err,
                    ))
            if not result["rows"]:
                errors.append(ValidationError(
                    document="ciq", error_type="empty_content",
                    message="CIQ file contains no data rows",
                ))

    # Validate MOP
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
        else:
            result = parse_mop(mop_path)
            if result["errors"]:
                for err in result["errors"]:
                    errors.append(ValidationError(
                        document="mop", error_type="parse_error",
                        message=err,
                    ))
            if not result["steps"]:
                errors.append(ValidationError(
                    document="mop", error_type="empty_content",
                    message="MOP contains no extractable command steps",
                ))

    # Validate constraints (optional)
    if constraints_path and Path(constraints_path).exists():
        suffix = Path(constraints_path).suffix.lower()
        if suffix not in (".txt", ".yaml", ".yml", ".json", ".csv"):
            errors.append(ValidationError(
                document="constraints", error_type="invalid_format",
                message=f"Constraints file format not supported: {suffix}",
            ))

    # Validate command outputs
    if not Path(command_outputs_path).exists():
        errors.append(ValidationError(
            document="command_outputs", error_type="missing_file",
            message="Command outputs file not found",
        ))
    else:
        result = parse_command_outputs(command_outputs_path)
        if result["errors"]:
            for err in result["errors"]:
                errors.append(ValidationError(
                    document="command_outputs", error_type="parse_error",
                    message=err,
                ))

    return ValidationResult(valid=len(errors) == 0, errors=errors)
