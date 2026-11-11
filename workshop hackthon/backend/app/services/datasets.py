"""Dataset inspection and safe CSV upload handling.

Inspection reads only the header and counts physical records (it never loads the
file into pandas), so it stays fast for large files. Results are cached on
(path, size, mtime).

Upload safety
-------------
* the entity comes from a fixed whitelist and decides the stored file name
  (``<entity>.csv``) - the client's file name is never used as a path
* extension must be .csv; size is capped (MAX_UPLOAD_MB)
* the body is streamed to a temporary file in the target directory, checked
  (UTF-8, header row, required columns) and only then atomically renamed
"""

from __future__ import annotations

import csv
import os
import tempfile
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

from app.config.settings import Settings
from app.services.monitoring.logger import get_logger
from app.services.validation.schemas import ENTITY_LOAD_ORDER, ENTITY_SPECS

log = get_logger("datasets")

SOURCES: tuple[str, ...] = ("uploads", "generated", "sample", "invalid")
COLUMN_TYPES: dict[str, str] = {
    "customer_id": "VARCHAR(20)", "first_name": "VARCHAR(100)", "last_name": "VARCHAR(100)",
    "email": "VARCHAR(255)", "city": "VARCHAR(100)", "state": "VARCHAR(100)",
    "country": "VARCHAR(100)", "created_at": "TIMESTAMP", "updated_at": "TIMESTAMP",
    "product_id": "VARCHAR(20)", "product_name": "VARCHAR(200)", "category": "VARCHAR(100)",
    "brand": "VARCHAR(100)", "unit_price": "NUMERIC(12,2)", "order_id": "VARCHAR(30)",
    "order_date": "TIMESTAMP", "order_status": "VARCHAR(20)", "payment_method": "VARCHAR(30)",
    "order_item_id": "VARCHAR(30)", "quantity": "INTEGER", "discount_amount": "NUMERIC(12,2)",
}


class UploadError(ValueError):
    """The uploaded file was rejected (message is safe to show to the user)."""


@lru_cache(maxsize=256)
def _inspect_cached(path_str: str, size: int, mtime_ns: int) -> tuple[list[str], int]:
    """Return (header columns, record count) for a CSV file."""
    del size, mtime_ns  # part of the cache key only
    with open(path_str, "r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header: list[str] = []
        for row in reader:
            if any(cell.strip() for cell in row):
                header = [c.strip().lower() for c in row]
                break
        count = sum(1 for row in reader if row and any(cell.strip() for cell in row))
    return header, count


def inspect_file(entity: str, path: Path) -> dict:
    """Describe one entity file (existence, size, records, schema check)."""
    spec = ENTITY_SPECS[entity]
    columns = [{"name": c, "required": c in spec.required_columns,
                "type": COLUMN_TYPES.get(c, "TEXT")} for c in spec.columns]
    base = {"entity": entity, "file_name": spec.file_name, "columns": columns}
    if not path.is_file():
        return {**base, "exists": False, "status": "missing",
                "message": None, "found_columns": [], "missing_required_columns": [],
                "unexpected_columns": []}
    stat = path.stat()
    modified = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    if stat.st_size == 0:
        return {**base, "exists": True, "size_bytes": 0, "record_count": 0, "modified_at": modified,
                "status": "invalid", "message": "File is empty (0 bytes).", "found_columns": [],
                "missing_required_columns": list(spec.required_columns), "unexpected_columns": []}
    try:
        header, count = _inspect_cached(str(path), stat.st_size, stat.st_mtime_ns)
    except (UnicodeDecodeError, csv.Error) as exc:
        return {**base, "exists": True, "size_bytes": stat.st_size, "modified_at": modified,
                "status": "invalid", "message": f"Not a readable UTF-8 CSV file: {exc}",
                "found_columns": [], "missing_required_columns": [], "unexpected_columns": []}
    missing = [c for c in spec.required_columns if c not in header]
    unexpected = [c for c in header if c not in spec.columns]
    status = "invalid" if (missing or not header) else "ready"
    message = None
    if not header:
        message = "File has no header row."
    elif missing:
        message = f"Missing required column(s): {', '.join(missing)}."
    return {**base, "exists": True, "size_bytes": stat.st_size, "record_count": count,
            "modified_at": modified, "found_columns": header,
            "missing_required_columns": missing, "unexpected_columns": unexpected,
            "status": status, "message": message}


def list_datasets(settings: Settings) -> dict:
    """All four entities for every dataset source."""
    sources = []
    for source in SOURCES:
        directory = settings.dataset_dir(source)
        files = [inspect_file(entity, directory / ENTITY_SPECS[entity].file_name)
                 for entity in ENTITY_LOAD_ORDER]
        sources.append({"source": source, "directory": str(directory), "files": files})
    return {"sources": sources}


def save_upload(settings: Settings, entity: str, filename: str | None, stream: BinaryIO) -> dict:
    """Validate and store an uploaded CSV as ``uploads/<entity>.csv``."""
    if entity not in ENTITY_SPECS:
        raise UploadError(f"Unknown entity '{entity}'. Use one of: {', '.join(ENTITY_LOAD_ORDER)}.")
    if not filename or not filename.lower().endswith(".csv"):
        raise UploadError("Only .csv files can be uploaded.")
    limit = settings.max_upload_mb * 1024 * 1024
    target_dir = settings.dataset_dir("uploads")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / ENTITY_SPECS[entity].file_name

    fd, tmp_name = tempfile.mkstemp(prefix=f".{entity}-", suffix=".part", dir=target_dir)
    tmp = Path(tmp_name)
    try:
        written = 0
        with os.fdopen(fd, "wb") as out:
            while chunk := stream.read(1024 * 1024):
                written += len(chunk)
                if written > limit:
                    raise UploadError(f"File is larger than {settings.max_upload_mb} MB.")
                out.write(chunk)
        if written == 0:
            raise UploadError("The uploaded file is empty.")
        info = inspect_file(entity, tmp)
        if info["message"] and "UTF-8" in info["message"]:
            raise UploadError(info["message"])
        if info["status"] == "invalid":
            raise UploadError(info["message"] or "The file does not match the expected schema.")
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    log.info("Uploaded %s (%d bytes) -> %s", filename, written, target)
    return inspect_file(entity, target)
