"""Dimension transformations: customer, product and date.

Inputs are the *validated, normalised* string frames produced by the
validation layer, so every conversion here is guaranteed to succeed.

Missing optional field rules (documented in docs/data_dictionary.md)
---------------------------------------------------------------------
* customers.last_name    -> NULL (not every customer has a surname)
* customers.country      -> 'India'
* customers.updated_at   -> created_at
* products.brand         -> 'Unbranded'
* products.updated_at    -> created_at
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import numpy as np
import pandas as pd

from src.validation.schemas import DEFAULT_BRAND, DEFAULT_COUNTRY, TIMESTAMP_FORMAT

DIM_CUSTOMER_COLUMNS = ["customer_id", "first_name", "last_name", "email", "city", "state",
                        "country", "created_at", "updated_at"]
DIM_PRODUCT_COLUMNS = ["product_id", "product_name", "category", "brand", "unit_price",
                       "created_at", "updated_at"]
DIM_DATE_COLUMNS = ["date_key", "full_date", "day", "day_name", "week", "month", "month_name",
                    "quarter", "year", "is_weekend"]

UNBOUNDED_START = pd.Timestamp("1900-01-01 00:00:00")


def clean_text(values: pd.Series) -> pd.Series:
    """Trim and collapse internal whitespace runs to a single space."""
    return values.astype(str).str.replace(r"\s+", " ", regex=True).str.strip()


def normalize_case(values: pd.Series) -> pd.Series:
    """Title-case values that arrive in ALL CAPS or all lower case; keep mixed case as sent.

    'hyderabad' -> 'Hyderabad', 'TAMIL NADU' -> 'Tamil Nadu', but 'boAt' stays 'boAt'.
    """
    cleaned = clean_text(values)
    uniform = (cleaned == cleaned.str.lower()) | (cleaned == cleaned.str.upper())
    return cleaned.where(~uniform, cleaned.str.title())


def to_timestamp(values: pd.Series) -> pd.Series:
    """Parse validated 'YYYY-MM-DD HH:MM:SS' text (or ISO 'T') to datetime64[ns]; '' -> NaT."""
    text = values.astype(str).str.strip().str.replace("T", " ", regex=False)
    parsed = pd.to_datetime(text.where(text != ""), format=TIMESTAMP_FORMAT, errors="raise")
    return parsed.astype("datetime64[ns]")


def to_decimal(values: pd.Series) -> pd.Series:
    """Convert validated decimal text to ``Decimal`` with exactly two places ('' -> 0.00)."""
    quant = Decimal("0.01")
    return pd.Series([Decimal(v).quantize(quant) if v != "" else Decimal("0.00")
                      for v in values.astype(str).str.strip()], index=values.index, dtype=object)


def _optional(values: pd.Series, default: object) -> pd.Series:
    cleaned = clean_text(values)
    return cleaned.where(cleaned != "", default)


def transform_customers(valid_customers: pd.DataFrame) -> pd.DataFrame:
    """Build the customer dimension input (one row per business key, sorted)."""
    df = valid_customers
    created = to_timestamp(df["created_at"])
    updated = to_timestamp(df["updated_at"]).fillna(created)
    last_name = normalize_case(df["last_name"])
    out = pd.DataFrame({
        "customer_id": clean_text(df["customer_id"]),
        "first_name": normalize_case(df["first_name"]),
        "last_name": last_name.astype(object).where(last_name != "", None),
        "email": clean_text(df["email"]).str.lower(),
        "city": normalize_case(df["city"]),
        "state": normalize_case(df["state"]),
        "country": normalize_case(_optional(df["country"], DEFAULT_COUNTRY)),
        "created_at": created,
        "updated_at": updated,
    }, columns=DIM_CUSTOMER_COLUMNS)
    return out.sort_values("customer_id", kind="stable").reset_index(drop=True)


def transform_products(valid_products: pd.DataFrame) -> pd.DataFrame:
    """Build the product dimension input (one row per business key, sorted)."""
    df = valid_products
    created = to_timestamp(df["created_at"])
    updated = to_timestamp(df["updated_at"]).fillna(created)
    out = pd.DataFrame({
        "product_id": clean_text(df["product_id"]),
        "product_name": clean_text(df["product_name"]),
        "category": normalize_case(df["category"]),
        "brand": _optional(df["brand"], DEFAULT_BRAND),
        "unit_price": to_decimal(df["unit_price"]),
        "created_at": created,
        "updated_at": updated,
    }, columns=DIM_PRODUCT_COLUMNS)
    return out.sort_values("product_id", kind="stable").reset_index(drop=True)


def date_key_from_timestamp(values: pd.Series) -> pd.Series:
    """YYYYMMDD integer surrogate key for the date dimension (vectorised)."""
    ts = pd.to_datetime(values)
    return (ts.dt.year * 10000 + ts.dt.month * 100 + ts.dt.day).astype("int64")


def generate_date_dimension(start_date: date, end_date: date) -> pd.DataFrame:
    """One row per calendar day in [start_date, end_date] (inclusive)."""
    if end_date < start_date:
        raise ValueError("end_date must not be before start_date")
    days = pd.date_range(start_date, end_date, freq="D")
    iso_week = days.isocalendar().week.to_numpy().astype("int64")
    return pd.DataFrame({
        "date_key": (days.year * 10000 + days.month * 100 + days.day).astype("int64"),
        "full_date": days.date,
        "day": days.day.astype("int64"),
        "day_name": days.day_name(),
        "week": iso_week,
        "month": days.month.astype("int64"),
        "month_name": days.month_name(),
        "quarter": days.quarter.astype("int64"),
        "year": days.year.astype("int64"),
        "is_weekend": np.asarray(days.day_of_week >= 5),
    }, columns=DIM_DATE_COLUMNS)


def date_range_for(timestamps: pd.Series) -> tuple[date, date] | None:
    """Full calendar years covering the given timestamps (None when empty)."""
    ts = pd.to_datetime(timestamps).dropna()
    if ts.empty:
        return None
    return date(int(ts.min().year), 1, 1), date(int(ts.max().year), 12, 31)
