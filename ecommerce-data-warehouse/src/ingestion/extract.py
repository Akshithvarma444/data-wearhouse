"""Source extraction: read the four CSV extracts with structural checks.

Responsibilities
----------------
* file exists, has a ``.csv`` extension and is not empty (0 bytes / whitespace)
* header check: missing *required* columns -> ``SchemaMismatchError``;
  missing *optional* columns -> added as empty + WARNING;
  unexpected columns -> WARNING (or ``SchemaDriftError`` in strict mode)
* every record is preserved as text exactly as read (no type coercion here)
* rows whose field count differs from the header are NOT dropped - they are
  returned as ``malformed_rows`` so validation can quarantine them
* captures file name, SHA-256 checksum, size, record count and ingestion time
"""

from __future__ import annotations

import csv
import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.monitoring.logger import get_logger
from src.validation.schemas import ENTITY_LOAD_ORDER, ENTITY_SPECS, EntitySpec

log = get_logger("ingestion")

ROW_NUMBER_COLUMN = "source_row_number"


class IngestionError(Exception):
    """Base class for extraction failures (the whole run must stop)."""


class SourceFileNotFoundError(IngestionError):
    """The expected source file does not exist."""


class InvalidFileExtensionError(IngestionError):
    """The source file is not a .csv file."""


class EmptySourceFileError(IngestionError):
    """The source file is 0 bytes or contains no header."""


class SchemaMismatchError(IngestionError):
    """A required column is missing or the header is unusable."""


class SchemaDriftError(IngestionError):
    """Unexpected columns were found while running in strict-schema mode."""


@dataclass
class MalformedRow:
    """A physical CSV record whose field count does not match the header."""

    source_row_number: int
    fields: list[str]


@dataclass
class ExtractedEntity:
    """Result of extracting one source file.

    ``data`` holds one column per *expected* column (all ``str``), any unexpected
    columns, and ``source_row_number`` (the 1-based line number in the file).
    """

    entity_name: str
    source_file: str
    file_path: Path
    data: pd.DataFrame
    ingested_at: datetime
    file_sha256: str
    file_size_bytes: int
    unexpected_columns: list[str] = field(default_factory=list)
    missing_optional_columns: list[str] = field(default_factory=list)
    malformed_rows: list[MalformedRow] = field(default_factory=list)

    @property
    def record_count(self) -> int:
        """All input records: well-formed rows + malformed rows."""
        return len(self.data) + len(self.malformed_rows)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve_source_dir(source_dir: Path | str | None = None) -> Path:
    """CLI argument wins, then the SOURCE_DATA_DIR env var, then data/raw."""
    if source_dir is not None:
        return Path(source_dir)
    from src.config.settings import get_settings  # local import keeps module import cheap
    env_value = os.getenv("SOURCE_DATA_DIR")
    return Path(env_value) if env_value else get_settings().source_data_dir


def extract_entity(spec: EntitySpec, source_dir: Path | str | None = None,
                   strict_schema: bool = False, file_path: Path | None = None) -> ExtractedEntity:
    """Extract and structurally check one entity file."""
    path = Path(file_path) if file_path else resolve_source_dir(source_dir) / spec.file_name
    if not path.exists():
        raise SourceFileNotFoundError(f"{spec.name}: source file not found: {path}")
    if not path.is_file():
        raise SourceFileNotFoundError(f"{spec.name}: not a regular file: {path}")
    if path.suffix.lower() != ".csv":
        raise InvalidFileExtensionError(f"{spec.name}: expected a .csv file, got '{path.name}'")
    size = path.stat().st_size
    if size == 0:
        raise EmptySourceFileError(f"{spec.name}: source file is empty (0 bytes): {path}")

    ingested_at = datetime.now(timezone.utc)
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header: list[str] | None = None
        for raw_header in reader:
            if any(cell.strip() for cell in raw_header):
                header = [cell.strip().lower() for cell in raw_header]
                break
        if header is None:
            raise EmptySourceFileError(f"{spec.name}: file has no header row: {path}")
        if len(set(header)) != len(header):
            dupes = sorted({c for c in header if header.count(c) > 1})
            raise SchemaMismatchError(f"{spec.name}: duplicate column names {dupes} in {path.name}")

        missing_required = [c for c in spec.required_columns if c not in header]
        if missing_required:
            raise SchemaMismatchError(
                f"{spec.name}: missing required column(s) {missing_required} in {path.name}; "
                f"found {header}")

        width = len(header)
        rows: list[list[str]] = []
        row_numbers: list[int] = []
        malformed: list[MalformedRow] = []
        for fields in reader:
            if not fields or (len(fields) == 1 and fields[0].strip() == ""):
                continue  # blank line - not a record
            if len(fields) != width:
                malformed.append(MalformedRow(reader.line_num, fields))
                continue
            rows.append(fields)
            row_numbers.append(reader.line_num)

    frame = pd.DataFrame(rows, columns=header, dtype=object)
    missing_optional = [c for c in spec.columns if c not in header]
    for column in missing_optional:
        frame[column] = ""
    unexpected = [c for c in header if c not in spec.columns]
    frame[ROW_NUMBER_COLUMN] = pd.array(row_numbers, dtype="int64")
    ordered = list(spec.columns) + unexpected + [ROW_NUMBER_COLUMN]
    frame = frame[ordered]

    if missing_optional:
        log.warning("%s: optional column(s) %s absent in %s - treated as empty",
                    spec.name, missing_optional, path.name)
    if unexpected:
        message = (f"{spec.name}: unexpected column(s) {unexpected} in {path.name} "
                   f"(schema drift)")
        if strict_schema:
            raise SchemaDriftError(message)
        log.warning("%s - columns are preserved in raw.extra_attributes and ignored downstream",
                    message)
    if malformed:
        log.warning("%s: %d malformed row(s) (wrong field count) in %s - will be quarantined",
                    spec.name, len(malformed), path.name)

    entity = ExtractedEntity(
        entity_name=spec.name, source_file=path.name, file_path=path.resolve(), data=frame,
        ingested_at=ingested_at, file_sha256=_sha256(path), file_size_bytes=size,
        unexpected_columns=unexpected, missing_optional_columns=missing_optional,
        malformed_rows=malformed)
    log.info("Extracted %s: %d record(s) from %s (%d bytes)", spec.name, entity.record_count,
             path.name, size)
    return entity


def extract_customers(source_dir: Path | str | None = None, strict_schema: bool = False) -> ExtractedEntity:
    """Extract customers.csv."""
    return extract_entity(ENTITY_SPECS["customers"], source_dir, strict_schema)


def extract_products(source_dir: Path | str | None = None, strict_schema: bool = False) -> ExtractedEntity:
    """Extract products.csv."""
    return extract_entity(ENTITY_SPECS["products"], source_dir, strict_schema)


def extract_orders(source_dir: Path | str | None = None, strict_schema: bool = False) -> ExtractedEntity:
    """Extract orders.csv."""
    return extract_entity(ENTITY_SPECS["orders"], source_dir, strict_schema)


def extract_order_items(source_dir: Path | str | None = None, strict_schema: bool = False) -> ExtractedEntity:
    """Extract order_items.csv."""
    return extract_entity(ENTITY_SPECS["order_items"], source_dir, strict_schema)


def extract_all(source_dir: Path | str | None = None, strict_schema: bool = False
                ) -> dict[str, ExtractedEntity]:
    """Extract all four entities (fails fast on the first structural error)."""
    directory = resolve_source_dir(source_dir)
    if not directory.is_dir():
        raise SourceFileNotFoundError(f"source directory not found: {directory}")
    extractors = {
        "customers": extract_customers, "products": extract_products,
        "orders": extract_orders, "order_items": extract_order_items,
    }
    return {name: extractors[name](directory, strict_schema) for name in ENTITY_LOAD_ORDER}
