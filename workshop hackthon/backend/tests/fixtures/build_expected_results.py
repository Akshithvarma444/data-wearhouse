"""Build tests/fixtures/expected_sample_results.json from other/datasets/sample/*.csv.

The expected analytical results are computed here INDEPENDENTLY of the warehouse
(plain Python + Decimal over the source CSV files, no SQL, no pipeline code), so
test_analytics.py can prove that the star schema + SQL produce the same numbers.

    python -m tests.fixtures.build_expected_results
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT.parent / "other" / "datasets" / "sample"
OUTPUT = Path(__file__).with_name("expected_sample_results.json")
EXCLUDED = {"CANCELLED", "RETURNED"}


def _read(sample_dir: Path, name: str) -> list[dict[str, str]]:
    with (sample_dir / f"{name}.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def compute_expected(sample_dir: Path = SAMPLE) -> dict[str, object]:
    customers = {c["customer_id"]: c for c in _read(sample_dir, "customers")}
    products = {p["product_id"]: p for p in _read(sample_dir, "products")}
    orders = {o["order_id"]: o for o in _read(sample_dir, "orders")}
    lines = []
    for i in _read(sample_dir, "order_items"):
        o = orders[i["order_id"]]
        gross = int(i["quantity"]) * Decimal(i["unit_price"])
        net = gross - Decimal(i["discount_amount"] or "0")
        lines.append({**i, "gross": gross, "net": net, "status": o["order_status"],
                      "payment": o["payment_method"], "year": o["order_date"][:4],
                      "city": customers[o["customer_id"]]["city"],
                      "category": products[i["product_id"]]["category"]})
    recognised = [ln for ln in lines if ln["status"] not in EXCLUDED]

    def money(value: Decimal) -> str:
        return str(value.quantize(Decimal("0.01")))

    order_value: dict[str, Decimal] = defaultdict(Decimal)
    for ln in recognised:
        order_value[ln["order_id"]] += ln["net"]
    by = lambda key: _sum_by(recognised, key)  # noqa: E731

    units: dict[str, int] = defaultdict(int)
    net_by_product: dict[str, Decimal] = defaultdict(Decimal)
    for ln in recognised:
        units[ln["product_id"]] += int(ln["quantity"])
        net_by_product[ln["product_id"]] += ln["net"]
    top_products = sorted(units, key=lambda p: (-units[p], -net_by_product[p], p))[:10]

    status_orders: dict[str, set[str]] = defaultdict(set)
    for ln in lines:
        status_orders[ln["status"]].add(ln["order_id"])
    payment_orders: dict[str, set[str]] = defaultdict(set)
    for ln in recognised:
        payment_orders[ln["payment"]].add(ln["order_id"])

    total_net = sum((ln["net"] for ln in recognised), Decimal("0"))
    return {
        "total_revenue": {
            "orders": len({ln["order_id"] for ln in recognised}),
            "order_lines": len(recognised),
            "units_sold": sum(int(ln["quantity"]) for ln in recognised),
            "gross_sales": money(sum((ln["gross"] for ln in recognised), Decimal("0"))),
            "discounts": money(sum((Decimal(ln["discount_amount"] or "0") for ln in recognised),
                                   Decimal("0"))),
            "net_revenue": money(total_net),
        },
        "average_order_value": money((total_net / len(order_value)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP)),
        "yearly_revenue": {k: money(v) for k, v in sorted(by("year").items())},
        "category_revenue": {k: money(v) for k, v in sorted(by("category").items())},
        "city_revenue": {k: money(v) for k, v in sorted(by("city").items())},
        "revenue_by_payment_method": {k: money(v) for k, v in sorted(by("payment").items())},
        "orders_by_payment_method": {k: len(v) for k, v in sorted(payment_orders.items())},
        "order_status_orders": {k: len(v) for k, v in sorted(status_orders.items())},
        "top_10_products": top_products,
        "all_lines_net_revenue": money(sum((ln["net"] for ln in lines), Decimal("0"))),
    }


def _sum_by(lines: list[dict[str, object]], key: str) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for ln in lines:
        totals[str(ln[key])] += ln["net"]  # type: ignore[operator]
    return totals


if __name__ == "__main__":
    OUTPUT.write_text(json.dumps(compute_expected(), indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUTPUT}")
