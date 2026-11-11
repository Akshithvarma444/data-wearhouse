"""Source data contracts: column definitions, Pydantic record models and reason codes.

These definitions are the single source of truth used by ingestion (expected
columns), validation (row-level schema rules) and documentation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints
from pydantic_core import PydanticCustomError

# ----------------------------------------------------------------------------- constants
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}$")
EMAIL_PATTERN = r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$"

ORDER_STATUSES: tuple[str, ...] = ("PLACED", "SHIPPED", "DELIVERED", "CANCELLED", "RETURNED")
PAYMENT_METHODS: tuple[str, ...] = ("UPI", "CREDIT_CARD", "DEBIT_CARD", "NET_BANKING",
                                    "CASH_ON_DELIVERY", "WALLET", "UNKNOWN")

DEFAULT_COUNTRY = "India"
DEFAULT_BRAND = "Unbranded"
DEFAULT_PAYMENT_METHOD = "UNKNOWN"


class ReasonCode:
    """Stable machine-readable rejection reason codes (stored in ops.rejected_records)."""

    MISSING_REQUIRED_FIELD = "MISSING_REQUIRED_FIELD"
    VALUE_TOO_LONG = "VALUE_TOO_LONG"
    INVALID_EMAIL = "INVALID_EMAIL"
    INVALID_TIMESTAMP = "INVALID_TIMESTAMP"
    INVALID_NUMBER = "INVALID_NUMBER"
    INVALID_DECIMAL_SCALE = "INVALID_DECIMAL_SCALE"
    VALUE_OUT_OF_RANGE = "VALUE_OUT_OF_RANGE"
    NEGATIVE_VALUE = "NEGATIVE_VALUE"
    NON_POSITIVE_QUANTITY = "NON_POSITIVE_QUANTITY"
    INVALID_ALLOWED_VALUE = "INVALID_ALLOWED_VALUE"
    INVALID_FORMAT = "INVALID_FORMAT"
    TIMESTAMP_ORDER = "TIMESTAMP_ORDER"
    EXACT_DUPLICATE = "EXACT_DUPLICATE"
    CONFLICTING_DUPLICATE = "CONFLICTING_DUPLICATE"
    DUPLICATE_EMAIL = "DUPLICATE_EMAIL"
    UNKNOWN_CUSTOMER_REFERENCE = "UNKNOWN_CUSTOMER_REFERENCE"
    UNKNOWN_ORDER_REFERENCE = "UNKNOWN_ORDER_REFERENCE"
    UNKNOWN_PRODUCT_REFERENCE = "UNKNOWN_PRODUCT_REFERENCE"
    EXCESSIVE_DISCOUNT = "EXCESSIVE_DISCOUNT"
    MALFORMED_ROW = "MALFORMED_ROW"


# ----------------------------------------------------------------------------- reusable types
def _empty_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


_DECIMAL_TEXT_RE = re.compile(r"^(-?)(\d+)(?:\.(\d+))?$")
MONEY_MAX_WHOLE_DIGITS = 10  # NUMERIC(12,2)


def _check_money_text(value: Any) -> Any:
    """Validate money text in ONE pass (fast path; no Decimal arithmetic per row).

    Plain decimal notation only (rejects '1e2', 'NaN', '1,000', currency symbols),
    non-negative, at most 2 decimal places, at most 10 whole digits. The raised
    error types map to the reason codes in ``validate._reason_for_pydantic_error``.
    """
    if value is None:  # optional field already converted from '' by _empty_to_none
        return None
    if not isinstance(value, str):
        raise PydanticCustomError("decimal_parsing", "must be a plain decimal number")
    match = _DECIMAL_TEXT_RE.match(value.strip())
    if match is None:
        raise PydanticCustomError("decimal_parsing",
                                  "must be a plain decimal number such as 1499.00")
    sign, whole, fraction = match.groups()
    if sign and (whole.strip("0") or (fraction or "").strip("0")):
        raise PydanticCustomError("greater_than_equal", "must be greater than or equal to 0")
    if fraction is not None and len(fraction) > 2:
        raise PydanticCustomError("decimal_max_places", "must have at most 2 decimal places")
    if len(whole.lstrip("0")) > MONEY_MAX_WHOLE_DIGITS:
        raise PydanticCustomError("decimal_max_digits", "too many digits for NUMERIC(12,2)")
    return value.strip()


def _check_timestamp_text(value: Any) -> Any:
    """Only 'YYYY-MM-DD HH:MM:SS' (or ISO 'T' separator) is accepted; calendar is checked by pydantic."""
    if isinstance(value, str) and not _TIMESTAMP_RE.match(value):
        raise ValueError(f"timestamp must match {TIMESTAMP_FORMAT}")
    return value


RequiredId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=20)]
RequiredLongId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
RequiredText100 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
RequiredText200 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
OptionalText100 = Annotated[str | None, BeforeValidator(_empty_to_none),
                            Field(default=None, max_length=100)]
EmailText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=255,
                                             pattern=EMAIL_PATTERN)]
Timestamp = Annotated[datetime, BeforeValidator(_check_timestamp_text)]
OptionalTimestamp = Annotated[datetime | None, BeforeValidator(_check_timestamp_text),
                              BeforeValidator(_empty_to_none)]
# Money stays text after validation (exact); conversion to Decimal happens in transformation.
Money = Annotated[str, BeforeValidator(_check_money_text)]
OptionalMoney = Annotated[str | None, BeforeValidator(_check_money_text),
                          BeforeValidator(_empty_to_none), Field(default=None)]
Quantity = Annotated[int, Field(gt=0, le=100_000)]


# ----------------------------------------------------------------------------- record models
class _SourceRecord(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class CustomerRecord(_SourceRecord):
    customer_id: RequiredId
    first_name: RequiredText100
    last_name: OptionalText100 = None
    email: EmailText
    city: RequiredText100
    state: RequiredText100
    country: OptionalText100 = None
    created_at: Timestamp
    updated_at: OptionalTimestamp = None


class ProductRecord(_SourceRecord):
    product_id: RequiredId
    product_name: RequiredText200
    category: RequiredText100
    brand: OptionalText100 = None
    unit_price: Money
    created_at: Timestamp
    updated_at: OptionalTimestamp = None


class OrderRecord(_SourceRecord):
    order_id: RequiredLongId
    customer_id: RequiredId
    order_date: Timestamp
    order_status: Literal["PLACED", "SHIPPED", "DELIVERED", "CANCELLED", "RETURNED"]
    payment_method: Annotated[
        Literal["UPI", "CREDIT_CARD", "DEBIT_CARD", "NET_BANKING", "CASH_ON_DELIVERY", "WALLET",
                "UNKNOWN"] | None,
        BeforeValidator(_empty_to_none)] = None
    updated_at: OptionalTimestamp = None


class OrderItemRecord(_SourceRecord):
    order_item_id: RequiredLongId
    order_id: RequiredLongId
    product_id: RequiredId
    quantity: Quantity
    unit_price: Money
    discount_amount: OptionalMoney = None
    created_at: Timestamp


# ----------------------------------------------------------------------------- entity contracts
@dataclass(frozen=True)
class EntitySpec:
    """Contract for one source entity."""

    name: str
    file_name: str
    business_key: str
    columns: tuple[str, ...]
    required_columns: tuple[str, ...]
    model: type[BaseModel]
    timestamp_columns: tuple[str, ...]


ENTITY_SPECS: dict[str, EntitySpec] = {
    "customers": EntitySpec(
        name="customers", file_name="customers.csv", business_key="customer_id",
        columns=("customer_id", "first_name", "last_name", "email", "city", "state", "country",
                 "created_at", "updated_at"),
        required_columns=("customer_id", "first_name", "email", "city", "state", "created_at"),
        model=CustomerRecord, timestamp_columns=("created_at", "updated_at")),
    "products": EntitySpec(
        name="products", file_name="products.csv", business_key="product_id",
        columns=("product_id", "product_name", "category", "brand", "unit_price", "created_at",
                 "updated_at"),
        required_columns=("product_id", "product_name", "category", "unit_price", "created_at"),
        model=ProductRecord, timestamp_columns=("created_at", "updated_at")),
    "orders": EntitySpec(
        name="orders", file_name="orders.csv", business_key="order_id",
        columns=("order_id", "customer_id", "order_date", "order_status", "payment_method",
                 "updated_at"),
        required_columns=("order_id", "customer_id", "order_date", "order_status"),
        model=OrderRecord, timestamp_columns=("order_date", "updated_at")),
    "order_items": EntitySpec(
        name="order_items", file_name="order_items.csv", business_key="order_item_id",
        columns=("order_item_id", "order_id", "product_id", "quantity", "unit_price",
                 "discount_amount", "created_at"),
        required_columns=("order_item_id", "order_id", "product_id", "quantity", "unit_price",
                          "created_at"),
        model=OrderItemRecord, timestamp_columns=("created_at",)),
}

ENTITY_LOAD_ORDER: tuple[str, ...] = ("customers", "products", "orders", "order_items")
