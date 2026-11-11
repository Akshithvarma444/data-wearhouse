"""Fact transformations: order/order-item typing, measures and surrogate-key resolution.

Measures are computed with exact integer arithmetic in paise (1/100 INR):

    gross_sales = quantity * unit_price
    net_revenue = gross_sales - discount_amount

and returned as ``Decimal`` with two decimal places, so results are
deterministic and identical to PostgreSQL NUMERIC arithmetic.

Surrogate keys are resolved *point-in-time*: for every sale the dimension
version whose half-open range [effective_start_date, effective_end_date)
contains the order timestamp is chosen (``pandas.merge_asof``, vectorised).
The current flag is never used for resolution.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd

from src.transformation.dimensions import (clean_text, date_key_from_timestamp, to_decimal,
                                           to_timestamp)
from src.validation.schemas import DEFAULT_PAYMENT_METHOD

ORDER_COLUMNS = ["order_id", "customer_id", "order_timestamp", "order_status", "payment_method",
                 "updated_at"]
ORDER_ITEM_COLUMNS = ["order_item_id", "order_id", "product_id", "quantity", "unit_price",
                      "discount_amount", "created_at", "source_file"]
SALES_COLUMNS = ["order_item_id", "order_id", "customer_id", "product_id", "order_timestamp",
                 "date_key", "order_status", "payment_method", "quantity", "unit_price",
                 "gross_sales", "discount_amount", "net_revenue", "source_file"]
VERSION_COLUMNS = ["key", "business_id", "effective_start_date", "effective_end_date"]


class KeyResolutionError(RuntimeError):
    """Raised when facts cannot be linked to dimension versions."""


def transform_orders(valid_orders: pd.DataFrame) -> pd.DataFrame:
    """Type the validated orders. Missing payment_method -> 'UNKNOWN', updated_at -> order_date."""
    df = valid_orders
    order_ts = to_timestamp(df["order_date"])
    payment = clean_text(df["payment_method"]).str.upper()
    out = pd.DataFrame({
        "order_id": clean_text(df["order_id"]),
        "customer_id": clean_text(df["customer_id"]),
        "order_timestamp": order_ts,
        "order_status": clean_text(df["order_status"]).str.upper(),
        "payment_method": payment.where(payment != "", DEFAULT_PAYMENT_METHOD),
        "updated_at": to_timestamp(df["updated_at"]).fillna(order_ts),
    }, columns=ORDER_COLUMNS)
    return out.sort_values("order_id", kind="stable").reset_index(drop=True)


def transform_order_items(valid_items: pd.DataFrame, source_file: str) -> pd.DataFrame:
    """Type the validated order items. Missing discount_amount -> 0.00."""
    df = valid_items
    out = pd.DataFrame({
        "order_item_id": clean_text(df["order_item_id"]),
        "order_id": clean_text(df["order_id"]),
        "product_id": clean_text(df["product_id"]),
        "quantity": pd.to_numeric(df["quantity"].astype(str).str.strip()).astype("int64"),
        "unit_price": to_decimal(df["unit_price"]),
        "discount_amount": to_decimal(df["discount_amount"]),
        "created_at": to_timestamp(df["created_at"]),
        "source_file": source_file,
    }, columns=ORDER_ITEM_COLUMNS)
    return out.sort_values("order_item_id", kind="stable").reset_index(drop=True)


def _to_paise(values: pd.Series) -> np.ndarray:
    return np.fromiter((int(Decimal(v).scaleb(2)) for v in values), dtype=np.int64,
                       count=len(values))


def _from_paise(values: np.ndarray) -> list[Decimal]:
    return [Decimal(int(v)).scaleb(-2) for v in values]


def calculate_measures(sales: pd.DataFrame) -> pd.DataFrame:
    """Add gross_sales and net_revenue (exact, 2 dp). Raises if a rule is violated."""
    out = sales.copy()
    quantity = out["quantity"].to_numpy(dtype=np.int64)
    price = _to_paise(out["unit_price"])
    discount = _to_paise(out["discount_amount"])
    gross = quantity * price
    if (discount > gross).any() or (discount < 0).any() or (quantity <= 0).any():
        raise ValueError("measure rule violated: discount must be within [0, gross], quantity > 0")
    out["gross_sales"] = _from_paise(gross)
    out["net_revenue"] = _from_paise(gross - discount)
    out["unit_price"] = _from_paise(price)
    out["discount_amount"] = _from_paise(discount)
    return out


def transform_sales(order_items: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    """Join typed order lines to their order header and compute measures (fact grain).

    ``orders`` must contain a header for every ``order_items.order_id``.
    """
    if order_items.empty:
        return pd.DataFrame(columns=SALES_COLUMNS)
    headers = orders.drop_duplicates("order_id", keep="last")[
        ["order_id", "customer_id", "order_timestamp", "order_status", "payment_method"]]
    merged = order_items.merge(headers, on="order_id", how="left", validate="many_to_one")
    missing = merged["customer_id"].isna()
    if missing.any():
        sample = merged.loc[missing, "order_id"].unique()[:5].tolist()
        raise KeyResolutionError(f"{int(missing.sum())} order line(s) have no order header: {sample}")
    merged["order_timestamp"] = pd.to_datetime(merged["order_timestamp"]).astype("datetime64[ns]")
    merged["date_key"] = date_key_from_timestamp(merged["order_timestamp"])
    merged = calculate_measures(merged)
    return (merged[SALES_COLUMNS].sort_values("order_item_id", kind="stable")
            .reset_index(drop=True))


def _resolve_one(sales: pd.DataFrame, versions: pd.DataFrame, business_column: str) -> pd.Series:
    """Point-in-time lookup of one dimension. Returns surrogate keys (Int64, <NA> if none)."""
    result = pd.Series(pd.array([pd.NA] * len(sales), dtype="Int64"), index=sales.index)
    if sales.empty or versions.empty:
        return result
    left = pd.DataFrame({
        "_pos": np.arange(len(sales)),
        "business_id": sales[business_column].astype(str).to_numpy(),
        "ts": pd.to_datetime(sales["order_timestamp"]).astype("datetime64[ns]").to_numpy(),
    }).sort_values("ts", kind="stable")
    right = pd.DataFrame({
        "business_id": versions["business_id"].astype(str).to_numpy(),
        "key": versions["key"].astype("int64").to_numpy(),
        "start": pd.to_datetime(versions["effective_start_date"]).astype("datetime64[ns]").to_numpy(),
        "end": pd.to_datetime(versions["effective_end_date"]).astype("datetime64[ns]").to_numpy(),
    }).sort_values("start", kind="stable")
    matched = pd.merge_asof(left, right, left_on="ts", right_on="start", by="business_id",
                            direction="backward")
    in_range = matched["key"].notna() & (matched["end"].isna() | (matched["ts"] < matched["end"]))
    keys = matched["key"].where(in_range)
    result.iloc[matched["_pos"].to_numpy()] = pd.array(keys.to_numpy(), dtype="Int64")
    return result


def resolve_surrogate_keys(sales: pd.DataFrame, customer_versions: pd.DataFrame,
                           product_versions: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Attach customer_key / product_key valid at each order timestamp.

    ``*_versions`` frames have columns ``key, business_id, effective_start_date,
    effective_end_date`` (end is NaT for the open/current version).
    Returns ``(resolved, unresolved)``.
    """
    out = sales.copy()
    out["customer_key"] = _resolve_one(out, customer_versions, "customer_id")
    out["product_key"] = _resolve_one(out, product_versions, "product_id")
    unresolved_mask = out["customer_key"].isna() | out["product_key"].isna()
    resolved = out.loc[~unresolved_mask].reset_index(drop=True)
    resolved["customer_key"] = resolved["customer_key"].astype("int64")
    resolved["product_key"] = resolved["product_key"].astype("int64")
    return resolved, out.loc[unresolved_mask].reset_index(drop=True)
