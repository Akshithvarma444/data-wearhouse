"""Reproducible synthetic e-commerce source data generator (Faker + NumPy).

Produces the four transactional CSV extracts:

    customers.csv, products.csv, orders.csv, order_items.csv

Money is generated as integer paise (1/100 INR) and formatted to two decimal
places, so no floating-point error ever reaches the files. Every random choice
comes from one seeded ``numpy.random.Generator`` plus a seeded Faker instance,
so the same arguments always produce byte-identical files.

Clean data and deliberately invalid data are kept apart:
    * ``--output-dir`` (default data/raw)        -> clean, referentially valid data
    * ``--invalid``  (written to data/invalid)    -> clean base + injected defects,
      plus ``injected_defects.csv`` describing every defect and the reason code
      the validator is expected to raise.

Examples::

    python -m src.generators.generate_data --customers 1000 --products 200 --orders 10000 --seed 42
    python -m src.generators.generate_data --order-items 100000 --output-dir data/generated/items_100k
    python -m src.generators.generate_data --invalid
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

from src.config.settings import PROJECT_ROOT

CUSTOMER_COLUMNS = ["customer_id", "first_name", "last_name", "email", "city", "state",
                    "country", "created_at", "updated_at"]
PRODUCT_COLUMNS = ["product_id", "product_name", "category", "brand", "unit_price",
                   "created_at", "updated_at"]
ORDER_COLUMNS = ["order_id", "customer_id", "order_date", "order_status", "payment_method",
                 "updated_at"]
ORDER_ITEM_COLUMNS = ["order_item_id", "order_id", "product_id", "quantity", "unit_price",
                      "discount_amount", "created_at"]

# (city, state, relative weight) - metros get more customers.
INDIAN_CITIES: list[tuple[str, str, float]] = [
    ("Mumbai", "Maharashtra", 9), ("New Delhi", "Delhi", 9), ("Bengaluru", "Karnataka", 9),
    ("Hyderabad", "Telangana", 8), ("Chennai", "Tamil Nadu", 8), ("Kolkata", "West Bengal", 6),
    ("Pune", "Maharashtra", 6), ("Ahmedabad", "Gujarat", 5), ("Jaipur", "Rajasthan", 4),
    ("Lucknow", "Uttar Pradesh", 4), ("Noida", "Uttar Pradesh", 3), ("Kochi", "Kerala", 3),
    ("Coimbatore", "Tamil Nadu", 3), ("Visakhapatnam", "Andhra Pradesh", 3),
    ("Vijayawada", "Andhra Pradesh", 2), ("Indore", "Madhya Pradesh", 3),
    ("Bhopal", "Madhya Pradesh", 2), ("Nagpur", "Maharashtra", 2), ("Surat", "Gujarat", 3),
    ("Chandigarh", "Chandigarh", 2), ("Bhubaneswar", "Odisha", 2), ("Patna", "Bihar", 2),
    ("Guwahati", "Assam", 2), ("Mysuru", "Karnataka", 2), ("Thiruvananthapuram", "Kerala", 2),
]

# category -> (items, brands, min price INR, max price INR)
PRODUCT_CATALOG: dict[str, tuple[list[str], list[str], int, int]] = {
    "Electronics": (["Smartphone", "Bluetooth Earbuds", "Smartwatch", "Power Bank", "Laptop",
                     "Tablet", "Wireless Speaker", "LED Monitor"],
                    ["Samsung", "OnePlus", "Xiaomi", "boAt", "Lenovo", "HP", "Realme", "Noise"],
                    499, 79999),
    "Fashion": (["Cotton Kurta", "Denim Jeans", "Running Shoes", "Silk Saree", "Polo T-Shirt",
                 "Sneakers", "Winter Jacket", "Ethnic Dupatta"],
                ["FabIndia", "Biba", "Allen Solly", "Puma", "Adidas", "Levi's", "W", "Manyavar"],
                299, 6999),
    "Home & Kitchen": (["Pressure Cooker", "Mixer Grinder", "Non-Stick Tawa", "Water Purifier",
                        "Bedsheet Set", "Dinner Set", "Air Fryer"],
                       ["Prestige", "Hawkins", "Bajaj", "Philips", "Milton", "Pigeon", "Butterfly"],
                       199, 19999),
    "Beauty & Personal Care": (["Face Wash", "Hair Oil", "Sunscreen", "Beard Trimmer",
                                "Moisturiser", "Herbal Shampoo"],
                               ["Himalaya", "Mamaearth", "Lakme", "Biotique", "Nivea", "Philips"],
                               99, 3999),
    "Books": (["Novel", "Exam Guide", "Cookbook", "Children's Story Book", "Biography"],
              ["Penguin", "HarperCollins", "Arihant", "Rupa", "Scholastic"], 149, 1999),
    "Grocery": (["Basmati Rice 5kg", "Toor Dal 1kg", "Cold Pressed Oil 1L", "Masala Tea 500g",
                 "Dry Fruits Mix 500g"],
                ["India Gate", "Tata Sampann", "Fortune", "Tata Tea", "Happilo"], 89, 2499),
    "Sports & Fitness": (["Yoga Mat", "Cricket Bat", "Dumbbell Set", "Badminton Racket",
                          "Cycling Helmet"],
                         ["Nivia", "SG", "Yonex", "Decathlon", "Cosco"], 249, 9999),
}
PRODUCT_VARIANTS = ["", "Pro", "Lite", "Max", "Plus", "Classic", "Neo", "Prime"]

ORDER_STATUSES = ["DELIVERED", "SHIPPED", "PLACED", "CANCELLED", "RETURNED"]
ORDER_STATUS_P = [0.70, 0.10, 0.07, 0.08, 0.05]
PAYMENT_METHODS = ["UPI", "CREDIT_CARD", "DEBIT_CARD", "CASH_ON_DELIVERY", "NET_BANKING", "WALLET"]
PAYMENT_P = [0.40, 0.18, 0.14, 0.15, 0.07, 0.06]
EMAIL_DOMAINS = ["gmail.com", "yahoo.co.in", "outlook.com", "rediffmail.com", "hotmail.com"]

AVG_ITEMS_PER_ORDER = 2.5


@dataclass(frozen=True)
class GeneratorConfig:
    """Parameters controlling dataset size and shape."""

    customers: int = 1000
    products: int = 200
    orders: int | None = 10000
    order_items: int | None = None  # exact order-item count; overrides ``orders``
    seed: int = 42
    start_date: date = date(2023, 1, 1)
    end_date: date = date(2025, 12, 31)

    def __post_init__(self) -> None:
        if self.customers <= 0 or self.products <= 0:
            raise ValueError("customers and products must be positive")
        if self.order_items is None and (self.orders is None or self.orders <= 0):
            raise ValueError("either orders or order_items must be a positive number")
        if self.order_items is not None and self.order_items <= 0:
            raise ValueError("order_items must be positive")
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")


# ----------------------------------------------------------------------------- helpers
def format_paise(paise: np.ndarray) -> np.ndarray:
    """Format integer paise (non-negative) as 'rupees.paise' strings, e.g. 149900 -> '1499.00'."""
    paise = np.asarray(paise, dtype=np.int64)
    rupees = (paise // 100).astype(str)
    cents = np.char.zfill((paise % 100).astype(str), 2)
    return np.char.add(np.char.add(rupees, "."), cents)


def format_timestamps(values: np.ndarray) -> np.ndarray:
    """Format datetime64[s] values as 'YYYY-MM-DD HH:MM:SS'."""
    text = np.datetime_as_string(values.astype("datetime64[s]"), unit="s")
    return np.char.replace(text, "T", " ")


def _name_pools(fake: Faker, size: int) -> tuple[np.ndarray, np.ndarray]:
    firsts = sorted({fake.first_name() for _ in range(size)})
    lasts = sorted({fake.last_name() for _ in range(size)})
    return np.array(firsts, dtype=object), np.array(lasts, dtype=object)


# ----------------------------------------------------------------------------- entities
def generate_customers(rng: np.random.Generator, fake: Faker, n: int,
                       start: np.datetime64) -> pd.DataFrame:
    """Generate ``n`` Indian customers, all created before ``start``."""
    firsts, lasts = _name_pools(fake, 600)
    first = rng.choice(firsts, n)
    last = rng.choice(lasts, n)
    weights = np.array([c[2] for c in INDIAN_CITIES], dtype=float)
    city_idx = rng.choice(len(INDIAN_CITIES), n, p=weights / weights.sum())
    ids = np.char.add("C", np.char.zfill(np.arange(1, n + 1).astype(str), 7))

    local = (pd.Series(first).str.lower().str.replace(r"[^a-z]", "", regex=True) + "."
             + pd.Series(last).str.lower().str.replace(r"[^a-z]", "", regex=True))
    domains = rng.choice(np.array(EMAIL_DOMAINS, dtype=object), n)
    email = local + np.char.add(".", np.arange(1, n + 1).astype(str)).astype(object) + "@" + domains

    created = start - rng.integers(30 * 86400, 730 * 86400, n).astype("timedelta64[s]")
    updated = created + rng.integers(0, 25 * 86400, n).astype("timedelta64[s]")
    return pd.DataFrame({
        "customer_id": ids,
        "first_name": first,
        "last_name": last,
        "email": email.to_numpy(),
        "city": [INDIAN_CITIES[i][0] for i in city_idx],
        "state": [INDIAN_CITIES[i][1] for i in city_idx],
        "country": "India",
        "created_at": format_timestamps(created),
        "updated_at": format_timestamps(updated),
    }, columns=CUSTOMER_COLUMNS)


def generate_products(rng: np.random.Generator, n: int, start: np.datetime64
                      ) -> tuple[pd.DataFrame, np.ndarray]:
    """Generate ``n`` products. Returns the frame and the list prices in paise."""
    categories = list(PRODUCT_CATALOG)
    cat_idx = rng.integers(0, len(categories), n)
    names, brands, cats, prices = [], [], [], np.empty(n, dtype=np.int64)
    log_draw = rng.random(n)
    item_draw = rng.random(n)
    brand_draw = rng.random(n)
    variant_draw = rng.integers(0, len(PRODUCT_VARIANTS), n)
    for i in range(n):
        category = categories[cat_idx[i]]
        items, brand_list, lo, hi = PRODUCT_CATALOG[category]
        item = items[int(item_draw[i] * len(items))]
        brand = brand_list[int(brand_draw[i] * len(brand_list))]
        variant = PRODUCT_VARIANTS[variant_draw[i]]
        names.append(f"{brand} {item}" + (f" {variant}" if variant else ""))
        brands.append(brand)
        cats.append(category)
        rupees = int(round(math.exp(math.log(lo) + log_draw[i] * (math.log(hi) - math.log(lo)))))
        rupees = max(lo, (rupees // 10) * 10 - 1)  # psychological pricing: ...9
        prices[i] = rupees * 100
    created = start - rng.integers(60 * 86400, 900 * 86400, n).astype("timedelta64[s]")
    updated = created + rng.integers(0, 40 * 86400, n).astype("timedelta64[s]")
    frame = pd.DataFrame({
        "product_id": np.char.add("P", np.char.zfill(np.arange(1, n + 1).astype(str), 6)),
        "product_name": names,
        "category": cats,
        "brand": brands,
        "unit_price": format_paise(prices),
        "created_at": format_timestamps(created),
        "updated_at": format_timestamps(updated),
    }, columns=PRODUCT_COLUMNS)
    return frame, prices


def _items_per_order(rng: np.random.Generator, config: GeneratorConfig) -> np.ndarray:
    """Number of lines per order (1..6). Trimmed so the total equals ``order_items`` if given."""
    if config.order_items is None:
        assert config.orders is not None
        return 1 + np.minimum(rng.poisson(1.5, config.orders), 5)
    target = config.order_items
    estimate = int(math.ceil(target / AVG_ITEMS_PER_ORDER * 1.2)) + 10
    counts = 1 + np.minimum(rng.poisson(1.5, estimate), 5)
    while counts.sum() < target:  # practically never loops; kept for correctness
        counts = np.concatenate([counts, 1 + np.minimum(rng.poisson(1.5, estimate), 5)])
    cumulative = np.cumsum(counts)
    last = int(np.searchsorted(cumulative, target))
    counts = counts[: last + 1].copy()
    counts[-1] -= int(cumulative[last] - target)
    return counts


def _order_day_weights(days: pd.DatetimeIndex) -> np.ndarray:
    """Seasonality: festive Oct/Nov peak, December bump, weekend lift, gentle growth trend."""
    month = days.month.to_numpy()
    weights = np.ones(len(days))
    weights[np.isin(month, [10, 11])] *= 1.6
    weights[month == 12] *= 1.2
    weights[days.day_of_week.to_numpy() >= 5] *= 1.15
    weights *= 1.0 + 0.4 * np.linspace(0.0, 1.0, len(days))
    return weights / weights.sum()


def generate_orders_and_items(rng: np.random.Generator, config: GeneratorConfig,
                              n_customers: int, product_prices: np.ndarray
                              ) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate orders and their order lines with valid references only."""
    counts = _items_per_order(rng, config)
    n_orders = len(counts)

    customer_weights = rng.gamma(0.8, 1.0, n_customers)
    customer_idx = rng.choice(n_customers, n_orders, p=customer_weights / customer_weights.sum())

    days = pd.date_range(config.start_date, config.end_date, freq="D")
    day_idx = rng.choice(len(days), n_orders, p=_order_day_weights(days))
    order_dates = (days.to_numpy().astype("datetime64[s]")[day_idx]
                   + rng.integers(0, 86400, n_orders).astype("timedelta64[s]"))
    order_dates = np.sort(order_dates)  # order ids increase with time, like a real OLTP sequence

    status = rng.choice(np.array(ORDER_STATUSES, dtype=object), n_orders, p=ORDER_STATUS_P)
    payment = rng.choice(np.array(PAYMENT_METHODS, dtype=object), n_orders, p=PAYMENT_P)
    lag = rng.integers(3600, 10 * 86400, n_orders).astype("timedelta64[s]")
    updated = np.where(status == "PLACED", order_dates, order_dates + lag)

    order_ids = np.char.add("O", np.char.zfill(np.arange(1, n_orders + 1).astype(str), 8))
    orders = pd.DataFrame({
        "order_id": order_ids,
        "customer_id": np.char.add("C", np.char.zfill((customer_idx + 1).astype(str), 7)),
        "order_date": format_timestamps(order_dates),
        "order_status": status,
        "payment_method": payment,
        "updated_at": format_timestamps(updated.astype("datetime64[s]")),
    }, columns=ORDER_COLUMNS)

    n_items = int(counts.sum())
    item_order = np.repeat(np.arange(n_orders), counts)
    popularity = rng.gamma(0.6, 1.0, len(product_prices))
    product_idx = rng.choice(len(product_prices), n_items, p=popularity / popularity.sum())
    quantity = rng.choice(np.array([1, 2, 3, 4, 5]), n_items, p=[0.68, 0.20, 0.07, 0.03, 0.02])

    price = product_prices[product_idx].copy()
    on_sale = rng.random(n_items) < 0.12  # line sold below list price
    sale_factor = rng.integers(85, 100, n_items)
    price[on_sale] = price[on_sale] * sale_factor[on_sale] // 100

    gross = quantity * price
    discounted = rng.random(n_items) < 0.35
    pct = rng.choice(np.array([5, 10, 15, 20, 25]), n_items)
    discount = np.where(discounted, gross * pct // 100, 0)

    items = pd.DataFrame({
        "order_item_id": np.char.add("OI", np.char.zfill(np.arange(1, n_items + 1).astype(str), 9)),
        "order_id": order_ids[item_order],
        "product_id": np.char.add("P", np.char.zfill((product_idx + 1).astype(str), 6)),
        "quantity": quantity.astype(str),
        "unit_price": format_paise(price),
        "discount_amount": format_paise(discount),
        "created_at": orders["order_date"].to_numpy()[item_order],
    }, columns=ORDER_ITEM_COLUMNS)
    return orders, items


def generate_dataset(config: GeneratorConfig) -> dict[str, pd.DataFrame]:
    """Generate all four entities for ``config`` (deterministic for a given seed)."""
    rng = np.random.default_rng(config.seed)
    fake = Faker("en_IN")
    fake.seed_instance(config.seed)
    start = np.datetime64(config.start_date.isoformat(), "s")
    customers = generate_customers(rng, fake, config.customers, start)
    products, prices = generate_products(rng, config.products, start)
    orders, items = generate_orders_and_items(rng, config, config.customers, prices)
    return {"customers": customers, "products": products, "orders": orders, "order_items": items}


def write_dataset(frames: dict[str, pd.DataFrame], output_dir: Path) -> dict[str, Path]:
    """Write each entity to ``<output_dir>/<entity>.csv`` (UTF-8, LF line endings)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for entity, frame in frames.items():
        path = output_dir / f"{entity}.csv"
        frame.to_csv(path, index=False, lineterminator="\n")
        paths[entity] = path
    return paths


# ----------------------------------------------------------------------------- invalid data
def generate_invalid_dataset(output_dir: Path, seed: int = 42) -> pd.DataFrame:
    """Write a small dataset with deliberately injected defects + a defect manifest.

    The clean part is fully valid; every injected record is listed in
    ``injected_defects.csv`` with the reason code the validator must raise.
    Returns the manifest.
    """
    frames = generate_dataset(GeneratorConfig(customers=30, products=15, orders=None,
                                              order_items=60, seed=seed))
    customers, products = frames["customers"], frames["products"]
    orders, items = frames["orders"], frames["order_items"]
    # Own id/e-mail namespace ("X" prefix) so this test batch can be loaded into the
    # same warehouse as the clean datasets without colliding with their keys.
    customers["customer_id"] = "CX" + customers["customer_id"].str[1:]
    customers["email"] = "x." + customers["email"]
    products["product_id"] = "PX" + products["product_id"].str[1:]
    orders["order_id"] = "OX" + orders["order_id"].str[1:]
    orders["customer_id"] = "CX" + orders["customer_id"].str[1:]
    items["order_item_id"] = "OIX" + items["order_item_id"].str[2:]
    items["order_id"] = "OX" + items["order_id"].str[1:]
    items["product_id"] = "PX" + items["product_id"].str[1:]
    manifest: list[dict[str, str]] = []

    def defect(entity: str, record_id: str, description: str, code: str) -> None:
        manifest.append({"entity": entity, "source_record_id": record_id,
                         "defect": description, "expected_reason_code": code})

    base_c = customers.iloc[0].to_dict()
    ts = "2022-06-01 10:00:00"
    bad_customers = [
        {**base_c, "customer_id": "", "email": "missing.id@gmail.com"},
        {**base_c, "customer_id": "C9000002", "email": "ravi.kumar_at_gmail.com"},
        customers.iloc[1].to_dict(),  # exact duplicate of a clean row
        {**base_c, "customer_id": "C9000004", "email": "conflict.a@gmail.com", "city": "Hyderabad",
         "state": "Telangana", "created_at": ts, "updated_at": ts},
        {**base_c, "customer_id": "C9000004", "email": "conflict.a@gmail.com", "city": "Chennai",
         "state": "Tamil Nadu", "created_at": ts, "updated_at": ts},
        {**base_c, "customer_id": "C9000005", "email": "bad.date@gmail.com",
         "created_at": "2024-02-30 10:00:00", "updated_at": "2024-03-01 10:00:00"},
    ]
    defect("customers", "", "customer_id is empty", "MISSING_REQUIRED_FIELD")
    defect("customers", "C9000002", "email has no @", "INVALID_EMAIL")
    defect("customers", str(customers.iloc[1]["customer_id"]), "exact duplicate row", "EXACT_DUPLICATE")
    defect("customers", "C9000004", "same id, different city (2 rows)", "CONFLICTING_DUPLICATE")
    defect("customers", "C9000005", "created_at is 30 Feb", "INVALID_TIMESTAMP")

    base_p = products.iloc[0].to_dict()
    bad_products = [
        {**base_p, "product_id": "P900001", "unit_price": "-499.00"},
        {**base_p, "product_id": "P900002", "product_name": "   "},
        {**base_p, "product_id": "P900003", "unit_price": "abc"},
    ]
    defect("products", "P900001", "negative unit_price", "NEGATIVE_VALUE")
    defect("products", "P900002", "blank product_name", "MISSING_REQUIRED_FIELD")
    defect("products", "P900003", "non-numeric unit_price", "INVALID_NUMBER")

    base_o = orders.iloc[0].to_dict()
    bad_orders = [
        {**base_o, "order_id": "O90000001", "customer_id": "C9999999"},
        {**base_o, "order_id": "O90000002", "order_status": "SHIPPED_MAYBE"},
        {**base_o, "order_id": "O90000003", "order_date": "31/02/2024"},
    ]
    defect("orders", "O90000001", "customer does not exist", "UNKNOWN_CUSTOMER_REFERENCE")
    defect("orders", "O90000002", "status not in allowed set", "INVALID_ALLOWED_VALUE")
    defect("orders", "O90000003", "order_date not a valid timestamp", "INVALID_TIMESTAMP")

    base_i = items.iloc[0].to_dict()
    bad_items = [
        items.iloc[1].to_dict(),  # exact duplicate
        {**base_i, "order_item_id": "OI900000001", "quantity": "1"},
        {**base_i, "order_item_id": "OI900000001", "quantity": "2"},
        {**base_i, "order_item_id": "OI900000002", "quantity": "-1"},
        {**base_i, "order_item_id": "OI900000003", "quantity": "0"},
        {**base_i, "order_item_id": "OI900000004", "unit_price": "-10.00"},
        {**base_i, "order_item_id": "OI900000005", "quantity": "1", "unit_price": "100.00",
         "discount_amount": "150.00"},
        {**base_i, "order_item_id": "OI900000006", "product_id": "P999999"},
        {**base_i, "order_item_id": "OI900000007", "order_id": "O99999999"},
        {**base_i, "order_item_id": "OI900000008", "order_id": "O90000002"},
        {**base_i, "order_item_id": "OI900000009", "quantity": "two"},
        {**base_i, "order_item_id": "OI900000010", "discount_amount": "-5.00"},
    ]
    defect("order_items", str(items.iloc[1]["order_item_id"]), "exact duplicate row", "EXACT_DUPLICATE")
    defect("order_items", "OI900000001", "same id, different quantity (2 rows)", "CONFLICTING_DUPLICATE")
    defect("order_items", "OI900000002", "negative quantity", "NON_POSITIVE_QUANTITY")
    defect("order_items", "OI900000003", "zero quantity", "NON_POSITIVE_QUANTITY")
    defect("order_items", "OI900000004", "negative unit_price", "NEGATIVE_VALUE")
    defect("order_items", "OI900000005", "discount 150.00 > gross 100.00", "EXCESSIVE_DISCOUNT")
    defect("order_items", "OI900000006", "product does not exist", "UNKNOWN_PRODUCT_REFERENCE")
    defect("order_items", "OI900000007", "order does not exist", "UNKNOWN_ORDER_REFERENCE")
    defect("order_items", "OI900000008", "parent order O90000002 is itself rejected",
           "UNKNOWN_ORDER_REFERENCE")
    defect("order_items", "OI900000009", "non-numeric quantity", "INVALID_NUMBER")
    defect("order_items", "OI900000010", "negative discount", "NEGATIVE_VALUE")
    defect("order_items", "OI-BAD-ROW", "row has 3 fields instead of 7", "MALFORMED_ROW")

    out = {
        "customers": pd.concat([customers, pd.DataFrame(bad_customers)], ignore_index=True),
        "products": pd.concat([products, pd.DataFrame(bad_products)], ignore_index=True),
        "orders": pd.concat([orders, pd.DataFrame(bad_orders)], ignore_index=True),
        "order_items": pd.concat([items, pd.DataFrame(bad_items)], ignore_index=True),
    }
    paths = write_dataset(out, output_dir)
    with paths["order_items"].open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("OI-BAD-ROW,OX0000001,PX00001\n")  # malformed: wrong field count

    manifest_frame = pd.DataFrame(manifest)
    manifest_frame.to_csv(output_dir / "injected_defects.csv", index=False, lineterminator="\n")
    return manifest_frame


# ----------------------------------------------------------------------------- CLI
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate synthetic e-commerce source CSVs.")
    parser.add_argument("--customers", type=int, default=1000)
    parser.add_argument("--products", type=int, default=200)
    parser.add_argument("--orders", type=int, default=None,
                        help="number of orders (default 10000 when --order-items is not given)")
    parser.add_argument("--order-items", type=int, default=None,
                        help="exact number of order-item rows (overrides --orders)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--start-date", type=date.fromisoformat, default=date(2023, 1, 1))
    parser.add_argument("--end-date", type=date.fromisoformat, default=date(2025, 12, 31))
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "data" / "raw")
    parser.add_argument("--invalid", action="store_true",
                        help="write the deliberately-invalid test dataset instead (data/invalid)")
    parser.add_argument("--invalid-dir", type=Path, default=PROJECT_ROOT / "data" / "invalid")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started = time.perf_counter()
    if args.invalid:
        manifest = generate_invalid_dataset(args.invalid_dir, seed=args.seed)
        print(f"Invalid test dataset written to {args.invalid_dir} "
              f"({len(manifest)} injected defects, see injected_defects.csv)")
        return 0
    orders = args.orders if (args.orders is not None or args.order_items is not None) else 10000
    config = GeneratorConfig(customers=args.customers, products=args.products, orders=orders,
                             order_items=args.order_items, seed=args.seed,
                             start_date=args.start_date, end_date=args.end_date)
    frames = generate_dataset(config)
    paths = write_dataset(frames, args.output_dir)
    elapsed = time.perf_counter() - started
    for entity, path in paths.items():
        print(f"{entity:<12} {len(frames[entity]):>10,} rows -> {path}")
    print(f"Generated in {elapsed:.2f}s (seed={args.seed})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
