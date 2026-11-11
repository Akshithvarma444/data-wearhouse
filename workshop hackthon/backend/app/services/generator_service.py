"""Synthetic data generation for the API (wraps app.generators.generate_data)."""

from __future__ import annotations

import io
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.config.settings import Settings
from app.generators.generate_data import (GeneratorConfig, generate_dataset,
                                          generate_invalid_dataset, write_dataset)
from app.services.monitoring.logger import get_logger
from app.services.validation.schemas import ENTITY_LOAD_ORDER, ENTITY_SPECS

log = get_logger("generator")


def _count_rows(path: Path) -> int:
    with path.open("r", encoding="utf-8") as handle:
        return max(sum(1 for _ in handle) - 1, 0)


def generate(settings: Settings, customers: int, products: int, orders: int, seed: int,
             include_invalid: bool) -> dict:
    """Generate the clean dataset into datasets/generated (replacing it) and optionally
    the invalid dataset into datasets/invalid. Returns real counts read back from disk."""
    started = time.perf_counter()
    out_dir = settings.dataset_dir("generated")
    config = GeneratorConfig(customers=customers, products=products, orders=orders, seed=seed)
    frames = generate_dataset(config)
    write_dataset(frames, out_dir)
    counts = {entity: len(frames[entity]) for entity in ENTITY_LOAD_ORDER}

    invalid_counts = None
    defects = None
    if include_invalid:
        invalid_dir = settings.dataset_dir("invalid")
        manifest = generate_invalid_dataset(invalid_dir, seed=seed)
        invalid_counts = {e: _count_rows(invalid_dir / ENTITY_SPECS[e].file_name)
                          for e in ENTITY_LOAD_ORDER}
        defects = len(manifest)
    duration = time.perf_counter() - started
    log.info("Generated dataset %s in %.2fs (seed=%d)", counts, duration, seed)
    return {"output_dir": str(out_dir), "counts": counts, "invalid_counts": invalid_counts,
            "injected_defects": defects, "duration_seconds": round(duration, 3),
            "generated_at": datetime.now(timezone.utc)}


def zip_dataset(settings: Settings, dataset: str) -> tuple[bytes, str]:
    """Zip the CSV files (and defect manifest) of a dataset folder in memory."""
    directory = settings.dataset_dir(dataset)
    names = [ENTITY_SPECS[e].file_name for e in ENTITY_LOAD_ORDER] + ["injected_defects.csv"]
    files = [directory / n for n in names if (directory / n).is_file()]
    if not files:
        raise FileNotFoundError(f"No files in the '{dataset}' dataset yet. Generate it first.")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, arcname=path.name)
    return buffer.getvalue(), f"{dataset}_dataset.zip"
