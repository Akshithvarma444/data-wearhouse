"""Data validation and quarantine classification.

Two layers, applied to every record:

1. **Schema validation (Pydantic)** - each row is validated against the entity's
   record model (required fields, types, timestamp format, numeric format,
   allowed values, length limits). Rows are validated in chunks of
   ``batch_size`` with one ``TypeAdapter`` call per chunk; *all* field errors
   are collected, not just the first one.
2. **Business / cross-record validation (vectorised pandas)** - duplicates,
   e-mail uniqueness, referential integrity, timestamp ordering and the
   discount <= gross rule.

A record is valid only if it has no reasons at all. Every invalid record is
returned as a ``RejectedRecord`` carrying *all* of its reasons, its source file,
row number, business key and the untouched original values.

Duplicate policy (deterministic)
--------------------------------
* Exact duplicate (same business key, identical normalised content): the first
  occurrence (lowest row number) is kept, later copies are quarantined as
  ``EXACT_DUPLICATE``.
* Conflicting duplicate (same business key, different content): **every** copy
  is quarantined as ``CONFLICTING_DUPLICATE`` - the pipeline never guesses which
  version is correct, and dependent records then fail their reference check.

Referential integrity
---------------------
A reference is satisfied by a *valid* record in the same batch or by a record
that already exists in the warehouse source layer (``ReferenceLookup``), which
is what allows late-arriving orders/items to reference earlier loads.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any, Protocol

import numpy as np
import pandas as pd
from pydantic import TypeAdapter, ValidationError

from app.services.ingestion.extract import ROW_NUMBER_COLUMN, ExtractedEntity
from app.services.monitoring.logger import get_logger
from app.services.validation.schemas import ENTITY_SPECS, TIMESTAMP_FORMAT, EntitySpec, ReasonCode

log = get_logger("validation")

NUMERIC_FIELDS = {"quantity", "unit_price", "discount_amount"}
_PAISE_RE = r"\d+(\.\d{1,2})?"


# ----------------------------------------------------------------------------- data classes
@dataclass
class RejectedRecord:
    """One quarantined source record (all reasons, full original content)."""

    entity_name: str
    source_file: str
    source_row_number: int
    source_record_id: str | None
    reason_codes: list[str]
    reasons: list[str]
    raw_record: dict[str, Any]

    @property
    def rejection_reason(self) -> str:
        return "; ".join(self.reasons)


@dataclass
class EntityValidationResult:
    """Validation output for one entity."""

    entity_name: str
    source_file: str
    input_count: int
    valid: pd.DataFrame
    rejected: list[RejectedRecord] = field(default_factory=list)
    skipped: pd.DataFrame | None = None  # valid but outside a backfill window

    @property
    def valid_count(self) -> int:
        return len(self.valid)

    @property
    def rejected_count(self) -> int:
        return len(self.rejected)

    @property
    def skipped_count(self) -> int:
        return 0 if self.skipped is None else len(self.skipped)

    def reason_counts(self) -> dict[str, int]:
        counts: dict[str, int] = defaultdict(int)
        for record in self.rejected:
            for code in set(record.reason_codes):
                counts[code] += 1
        return dict(sorted(counts.items()))


class ReferenceLookup(Protocol):
    """Looks up business keys that already exist in the warehouse source layer."""

    def existing_customer_ids(self, ids: set[str]) -> set[str]: ...

    def existing_product_ids(self, ids: set[str]) -> set[str]: ...

    def existing_order_ids(self, ids: set[str]) -> set[str]: ...

    def email_owners(self, emails: set[str]) -> dict[str, str]: ...


class InMemoryReferenceLookup:
    """Reference lookup backed by in-memory sets (used for tests / first loads)."""

    def __init__(self, customer_ids: Iterable[str] = (), product_ids: Iterable[str] = (),
                 order_ids: Iterable[str] = (), email_owners: dict[str, str] | None = None) -> None:
        self._customers = set(customer_ids)
        self._products = set(product_ids)
        self._orders = set(order_ids)
        self._emails = dict(email_owners or {})

    def existing_customer_ids(self, ids: set[str]) -> set[str]:
        return ids & self._customers

    def existing_product_ids(self, ids: set[str]) -> set[str]:
        return ids & self._products

    def existing_order_ids(self, ids: set[str]) -> set[str]:
        return ids & self._orders

    def email_owners(self, emails: set[str]) -> dict[str, str]:
        return {e: c for e, c in self._emails.items() if e in emails}


# ----------------------------------------------------------------------------- helpers
class _ReasonCollector:
    """Accumulates (code, message) pairs per positional row index."""

    def __init__(self) -> None:
        self.reasons: dict[int, list[tuple[str, str]]] = defaultdict(list)

    def add(self, position: int, code: str, message: str) -> None:
        entry = (code, message)
        if entry not in self.reasons[position]:
            self.reasons[position].append(entry)

    def add_mask(self, mask: np.ndarray | pd.Series, code: str, messages: pd.Series | str) -> None:
        positions = np.flatnonzero(np.asarray(mask, dtype=bool))
        if isinstance(messages, str):
            for pos in positions:
                self.add(int(pos), code, messages)
        else:
            values = messages.to_numpy()
            for pos in positions:
                self.add(int(pos), code, str(values[pos]))

    def rejected_mask(self, length: int) -> np.ndarray:
        mask = np.zeros(length, dtype=bool)
        if self.reasons:
            mask[list(self.reasons)] = True
        return mask


@lru_cache(maxsize=None)
def _adapter(entity_name: str) -> TypeAdapter:
    return TypeAdapter(list[ENTITY_SPECS[entity_name].model])  # type: ignore[valid-type]


def normalize_frame(entity_name: str, frame: pd.DataFrame) -> pd.DataFrame:
    """Return a normalised copy used for validation: trimmed text, canonical case for codes."""
    spec = ENTITY_SPECS[entity_name]
    out = pd.DataFrame(index=frame.index)
    for column in spec.columns:
        out[column] = frame[column].astype(object).fillna("").astype(str).str.strip()
    if "email" in out:
        out["email"] = out["email"].str.lower()
    for column in ("order_status", "payment_method"):
        if column in out:
            out[column] = out[column].str.upper()
    out[ROW_NUMBER_COLUMN] = frame[ROW_NUMBER_COLUMN].to_numpy()
    return out


def parse_timestamps(values: pd.Series) -> pd.Series:
    """Vectorised strict timestamp parse (invalid -> NaT). Accepts ' ' or 'T' separator."""
    cleaned = values.astype(str).str.strip().str.replace("T", " ", regex=False)
    return pd.to_datetime(cleaned, format=TIMESTAMP_FORMAT, errors="coerce")


def to_paise(values: pd.Series) -> pd.Series:
    """Convert decimal text with <= 2 places to integer paise (exact). Others -> <NA>."""
    text = values.astype(str).str.strip()
    ok = text.str.fullmatch(_PAISE_RE).fillna(False).to_numpy(dtype=bool)
    result = pd.Series(pd.array([pd.NA] * len(text), dtype="Int64"), index=values.index)
    if ok.any():
        parts = text[ok].str.split(".", n=1, expand=True)
        rupees = parts[0].astype("int64")
        frac = (parts[1].fillna("") if parts.shape[1] > 1 else pd.Series("", index=parts.index))
        frac = frac.astype(str).str.ljust(2, "0").astype("int64")
        result.loc[ok] = (rupees * 100 + frac).to_numpy()
    return result


def _reason_for_pydantic_error(spec: EntitySpec, field_name: str, error: dict[str, Any]) -> str:
    err_type = error.get("type", "")
    value = error.get("input")
    if err_type == "missing" or value is None or (isinstance(value, str) and value.strip() == ""):
        return ReasonCode.MISSING_REQUIRED_FIELD
    if err_type == "string_too_short":
        return ReasonCode.MISSING_REQUIRED_FIELD
    if err_type == "string_too_long":
        return ReasonCode.VALUE_TOO_LONG
    if field_name == "email":
        return ReasonCode.INVALID_EMAIL
    if field_name in spec.timestamp_columns:
        return ReasonCode.INVALID_TIMESTAMP
    if err_type == "literal_error":
        return ReasonCode.INVALID_ALLOWED_VALUE
    if err_type == "greater_than" and field_name == "quantity":
        return ReasonCode.NON_POSITIVE_QUANTITY
    if err_type == "greater_than_equal":
        return ReasonCode.NEGATIVE_VALUE
    if err_type == "decimal_max_places":
        return ReasonCode.INVALID_DECIMAL_SCALE
    if err_type in {"decimal_max_digits", "decimal_whole_digits", "less_than_equal"}:
        return ReasonCode.VALUE_OUT_OF_RANGE
    if field_name in NUMERIC_FIELDS:
        return ReasonCode.INVALID_NUMBER
    return ReasonCode.INVALID_FORMAT


def _schema_validate(spec: EntitySpec, frame: pd.DataFrame, collector: _ReasonCollector,
                     batch_size: int) -> None:
    """Row-level Pydantic validation in chunks; every field error is recorded."""
    adapter = _adapter(spec.name)
    columns = list(spec.columns)
    # plain lists + zip is ~20x faster than DataFrame.to_dict("records")
    values = [frame[c].to_numpy(dtype=object).tolist() for c in columns]
    for start in range(0, len(frame), batch_size):
        chunk = [dict(zip(columns, row))
                 for row in zip(*(v[start:start + batch_size] for v in values))]
        try:
            adapter.validate_python(chunk)
        except ValidationError as exc:
            for error in exc.errors(include_url=False, include_context=False):
                loc = error.get("loc", ())
                if len(loc) < 2:
                    continue
                position = start + int(loc[0])
                field_name = str(loc[1])
                code = _reason_for_pydantic_error(spec, field_name, error)
                shown = str(error.get("input"))[:60]
                message = ("is not a valid email address" if code == ReasonCode.INVALID_EMAIL
                           else "missing required value" if code == ReasonCode.MISSING_REQUIRED_FIELD
                           else str(error.get("msg")))
                collector.add(position, code, f"{field_name}: {message} (value='{shown}')")


def _duplicate_rules(spec: EntitySpec, frame: pd.DataFrame, collector: _ReasonCollector) -> int:
    """Exact vs conflicting duplicate detection on the business key. Returns #duplicate rows."""
    key = frame[spec.business_key]
    candidate = (key != "") & key.duplicated(keep=False)
    if not candidate.any():
        return 0
    subset = frame.loc[candidate, list(spec.columns)]
    content = pd.util.hash_pandas_object(subset, index=False)
    variants = content.groupby(subset[spec.business_key]).transform("nunique")
    counts = subset.groupby(spec.business_key)[spec.business_key].transform("size")
    first_row = (frame.loc[candidate, ROW_NUMBER_COLUMN]
                 .groupby(subset[spec.business_key]).transform("min"))

    conflicting = pd.Series(False, index=frame.index)
    conflicting.loc[subset.index] = (variants > 1).to_numpy()
    exact_later = pd.Series(False, index=frame.index)
    exact_later.loc[subset.index] = ((variants == 1) &
                                     subset[spec.business_key].duplicated(keep="first")).to_numpy()

    bk = spec.business_key
    conflict_msg = pd.Series("", index=frame.index, dtype=object)
    conflict_msg.loc[subset.index] = (f"{bk} '" + subset[bk] + "' appears "
                                      + counts.astype(str) + " times with different content")
    exact_msg = pd.Series("", index=frame.index, dtype=object)
    exact_msg.loc[subset.index] = ("exact duplicate of row " + first_row.astype(str)
                                   + f" ({bk}='" + subset[bk] + "')")
    collector.add_mask(conflicting.to_numpy(), ReasonCode.CONFLICTING_DUPLICATE, conflict_msg)
    collector.add_mask(exact_later.to_numpy(), ReasonCode.EXACT_DUPLICATE, exact_msg)
    return int(conflicting.sum() + exact_later.sum())


def _timestamp_order_rule(frame: pd.DataFrame, earlier: str, later: str,
                          collector: _ReasonCollector) -> None:
    start = parse_timestamps(frame[earlier])
    end = parse_timestamps(frame[later])
    bad = (start.notna() & end.notna() & (end < start)).to_numpy()
    if bad.any():
        collector.add_mask(bad, ReasonCode.TIMESTAMP_ORDER, f"{later} is earlier than {earlier}")


def _reference_rule(frame: pd.DataFrame, column: str, valid_ids: set[str],
                    lookup_fn: Any, code: str, target: str,
                    collector: _ReasonCollector) -> None:
    values = frame[column]
    present = values != ""
    referenced = set(values[present].unique())
    missing_from_batch = referenced - valid_ids
    known = lookup_fn(missing_from_batch) if missing_from_batch else set()
    allowed = pd.Index(sorted(valid_ids | known), dtype=object)
    bad = (present & ~values.isin(allowed)).to_numpy()
    if bad.any():
        messages = f"{column} '" + values + f"' not found among valid {target}"
        collector.add_mask(bad, code, messages)


def _build_result(spec: EntitySpec, extracted: ExtractedEntity, normalized: pd.DataFrame,
                  collector: _ReasonCollector) -> EntityValidationResult:
    rejected_mask = collector.rejected_mask(len(normalized))
    original_columns = [c for c in extracted.data.columns if c != ROW_NUMBER_COLUMN]
    rejected: list[RejectedRecord] = []
    if rejected_mask.any():
        positions = np.flatnonzero(rejected_mask)
        originals = extracted.data.iloc[positions]
        raw_records = originals[original_columns].to_dict("records")
        row_numbers = originals[ROW_NUMBER_COLUMN].to_numpy()
        keys = normalized[spec.business_key].to_numpy()[positions]
        for i, pos in enumerate(positions):
            reasons = collector.reasons[int(pos)]
            rejected.append(RejectedRecord(
                entity_name=spec.name, source_file=extracted.source_file,
                source_row_number=int(row_numbers[i]),
                source_record_id=str(keys[i]) or None,
                reason_codes=[code for code, _ in reasons],
                reasons=[message for _, message in reasons],
                raw_record=raw_records[i]))
    for bad in extracted.malformed_rows:
        rejected.append(RejectedRecord(
            entity_name=spec.name, source_file=extracted.source_file,
            source_row_number=bad.source_row_number,
            source_record_id=(bad.fields[0].strip() or None) if bad.fields else None,
            reason_codes=[ReasonCode.MALFORMED_ROW],
            reasons=[f"row has {len(bad.fields)} field(s), header has "
                     f"{len(original_columns)}"],
            raw_record={"raw_fields": bad.fields}))
    rejected.sort(key=lambda r: r.source_row_number)
    valid = normalized.loc[~rejected_mask].reset_index(drop=True)
    return EntityValidationResult(entity_name=spec.name, source_file=extracted.source_file,
                                  input_count=extracted.record_count, valid=valid,
                                  rejected=rejected)


# ----------------------------------------------------------------------------- entity validators
def validate_customers(extracted: ExtractedEntity, lookup: ReferenceLookup,
                       batch_size: int = 5000) -> EntityValidationResult:
    """Schema + business validation for customers."""
    spec = ENTITY_SPECS["customers"]
    frame = normalize_frame(spec.name, extracted.data)
    collector = _ReasonCollector()
    _schema_validate(spec, frame, collector, batch_size)
    _duplicate_rules(spec, frame, collector)
    _timestamp_order_rule(frame, "created_at", "updated_at", collector)

    # e-mail must be unique across *different* customers (in batch and vs. warehouse).
    has_email = (frame["email"] != "") & (frame["customer_id"] != "")
    ids_per_email = frame.loc[has_email].groupby("email")["customer_id"].transform("nunique")
    shared = pd.Series(False, index=frame.index)
    shared.loc[ids_per_email.index] = (ids_per_email > 1).to_numpy()
    collector.add_mask(shared.to_numpy(), ReasonCode.DUPLICATE_EMAIL,
                       "email '" + frame["email"] + "' is used by more than one customer_id")
    owners = lookup.email_owners(set(frame.loc[has_email, "email"].unique()))
    if owners:
        owner = frame["email"].map(owners)
        taken = (has_email & owner.notna() & (owner != frame["customer_id"])).to_numpy()
        collector.add_mask(taken, ReasonCode.DUPLICATE_EMAIL,
                           "email '" + frame["email"] + "' already belongs to customer "
                           + owner.fillna("").astype(str))
    return _build_result(spec, extracted, frame, collector)


def validate_products(extracted: ExtractedEntity, lookup: ReferenceLookup,
                      batch_size: int = 5000) -> EntityValidationResult:
    """Schema + business validation for products."""
    spec = ENTITY_SPECS["products"]
    frame = normalize_frame(spec.name, extracted.data)
    collector = _ReasonCollector()
    _schema_validate(spec, frame, collector, batch_size)
    _duplicate_rules(spec, frame, collector)
    _timestamp_order_rule(frame, "created_at", "updated_at", collector)
    return _build_result(spec, extracted, frame, collector)


def validate_orders(extracted: ExtractedEntity, valid_customer_ids: set[str],
                    lookup: ReferenceLookup, batch_size: int = 5000) -> EntityValidationResult:
    """Schema + business validation for orders (customer reference checked)."""
    spec = ENTITY_SPECS["orders"]
    frame = normalize_frame(spec.name, extracted.data)
    collector = _ReasonCollector()
    _schema_validate(spec, frame, collector, batch_size)
    _duplicate_rules(spec, frame, collector)
    _timestamp_order_rule(frame, "order_date", "updated_at", collector)
    _reference_rule(frame, "customer_id", valid_customer_ids, lookup.existing_customer_ids,
                    ReasonCode.UNKNOWN_CUSTOMER_REFERENCE, "customers", collector)
    return _build_result(spec, extracted, frame, collector)


def validate_order_items(extracted: ExtractedEntity, valid_order_ids: set[str],
                         valid_product_ids: set[str], lookup: ReferenceLookup,
                         batch_size: int = 5000) -> EntityValidationResult:
    """Schema + business validation for order items (order/product refs, discount rule)."""
    spec = ENTITY_SPECS["order_items"]
    frame = normalize_frame(spec.name, extracted.data)
    collector = _ReasonCollector()
    _schema_validate(spec, frame, collector, batch_size)
    _duplicate_rules(spec, frame, collector)
    _reference_rule(frame, "order_id", valid_order_ids, lookup.existing_order_ids,
                    ReasonCode.UNKNOWN_ORDER_REFERENCE, "orders", collector)
    _reference_rule(frame, "product_id", valid_product_ids, lookup.existing_product_ids,
                    ReasonCode.UNKNOWN_PRODUCT_REFERENCE, "products", collector)

    quantity = pd.to_numeric(frame["quantity"].where(frame["quantity"].str.fullmatch(r"\d+")),
                             errors="coerce").astype("Int64")
    price = to_paise(frame["unit_price"])
    discount_text = frame["discount_amount"].where(frame["discount_amount"] != "", "0")
    discount = to_paise(discount_text)
    gross = quantity * price
    excessive = (gross.notna() & discount.notna() & (discount > gross)).fillna(False)
    if excessive.any():
        messages = ("discount_amount " + discount_text + " exceeds gross sales "
                    + (gross.fillna(0) // 100).astype(str) + "."
                    + (gross.fillna(0) % 100).astype(str).str.zfill(2))
        collector.add_mask(excessive.to_numpy(dtype=bool), ReasonCode.EXCESSIVE_DISCOUNT, messages)
    return _build_result(spec, extracted, frame, collector)


def validate_all(extracted: dict[str, ExtractedEntity], lookup: ReferenceLookup,
                 batch_size: int = 5000) -> dict[str, EntityValidationResult]:
    """Validate all entities in dependency order (parents before children)."""
    customers = validate_customers(extracted["customers"], lookup, batch_size)
    products = validate_products(extracted["products"], lookup, batch_size)
    orders = validate_orders(extracted["orders"], set(customers.valid["customer_id"]), lookup,
                             batch_size)
    items = validate_order_items(extracted["order_items"], set(orders.valid["order_id"]),
                                 set(products.valid["product_id"]), lookup, batch_size)
    results = {"customers": customers, "products": products, "orders": orders,
               "order_items": items}
    for result in results.values():
        level = log.warning if result.rejected_count else log.info
        level("Validated %s: input=%d valid=%d rejected=%d %s", result.entity_name,
              result.input_count, result.valid_count, result.rejected_count,
              result.reason_counts() or "")
    return results
