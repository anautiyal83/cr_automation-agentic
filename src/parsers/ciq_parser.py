"""CIQ Excel parser — header-name-based parsing with multi-sheet and chunking support."""
from __future__ import annotations

from pathlib import Path
from typing import Iterator

import openpyxl


def parse_ciq(
    file_path: str | Path,
    selected_sheets: list[str] | None = None,
    excluded_columns: list[str] | None = None,
    mandatory_columns: list[str] | None = None,
    chunk_size: int = 100,
) -> dict:
    """Parse a CIQ Excel file into structured data.

    Returns:
        dict with keys: headers, rows, sheet_names, errors
    """
    wb = openpyxl.load_workbook(str(file_path), read_only=True, data_only=True)
    all_sheet_names = wb.sheetnames

    sheets_to_process = selected_sheets or all_sheet_names
    excluded = set(excluded_columns or [])

    all_headers: list[str] = []
    all_rows: list[dict[str, str]] = []
    errors: list[str] = []

    for sheet_name in sheets_to_process:
        if sheet_name not in all_sheet_names:
            errors.append(f"Sheet '{sheet_name}' not found in workbook")
            continue

        ws = wb[sheet_name]
        rows_iter = ws.iter_rows(values_only=True)

        # First row = headers
        header_row = next(rows_iter, None)
        if header_row is None:
            errors.append(f"Sheet '{sheet_name}' is empty")
            continue

        headers = [str(h).strip() if h is not None else "" for h in header_row]
        # Filter excluded columns
        col_indices = [(i, h) for i, h in enumerate(headers) if h and h not in excluded]
        sheet_headers = [h for _, h in col_indices]

        # Merge headers (union across sheets)
        for h in sheet_headers:
            if h not in all_headers:
                all_headers.append(h)

        # Parse data rows
        for row in rows_iter:
            row_dict = {}
            for idx, header in col_indices:
                val = row[idx] if idx < len(row) else None
                row_dict[header] = str(val).strip() if val is not None else ""
            if any(v for v in row_dict.values()):  # skip empty rows
                all_rows.append(row_dict)

    wb.close()

    # Validate mandatory columns
    if mandatory_columns:
        missing = [c for c in mandatory_columns if c not in all_headers]
        if missing:
            errors.append(f"Missing mandatory columns: {', '.join(missing)}")

    return {
        "headers": all_headers,
        "rows": all_rows,
        "sheet_names": all_sheet_names,
        "errors": errors,
    }


def parse_ciq_chunked(
    file_path: str | Path,
    chunk_size: int = 100,
    **kwargs,
) -> Iterator[dict]:
    """Parse CIQ in chunks for large files. Yields one chunk at a time."""
    result = parse_ciq(file_path, chunk_size=chunk_size, **kwargs)
    rows = result["rows"]
    headers = result["headers"]
    errors = result["errors"]

    for i in range(0, max(len(rows), 1), chunk_size):
        chunk_rows = rows[i:i + chunk_size]
        yield {
            "headers": headers,
            "rows": chunk_rows,
            "chunk_index": i // chunk_size,
            "total_rows": len(rows),
            "is_last_chunk": i + chunk_size >= len(rows),
            "errors": errors if i == 0 else [],
        }


def detect_field_types(headers: list[str], rows: list[dict]) -> dict[str, str]:
    """Auto-detect field types from column names and sample values."""
    type_map = {}

    name_hints = {
        "ip": "ip_address", "subnet": "ip_subnet", "mask": "ip_subnet",
        "email": "email", "mail": "email",
        "host": "hostname", "hostname": "hostname",
        "vlan": "integer", "port": "integer", "id": "integer",
        "phone": "phone", "tel": "phone",
        "date": "date",
    }

    for header in headers:
        lower = header.lower().replace("_", " ")
        detected = "string"  # default

        for hint, ftype in name_hints.items():
            if hint in lower:
                detected = ftype
                break

        # Check sample values for numeric
        if detected == "string" and rows:
            samples = [r.get(header, "") for r in rows[:10] if r.get(header)]
            if samples and all(s.replace(".", "").replace("-", "").isdigit() for s in samples):
                detected = "integer" if all("." not in s for s in samples) else "float"

        type_map[header] = detected

    return type_map
