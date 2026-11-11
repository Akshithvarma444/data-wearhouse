"""Business scenario demonstrations (also reused by the automated tests).

Scenario 1 - SCD2: customer C1 moves from Hyderabad to Chennai.
Scenario 2 - Late-arriving order: an April order arrives after the June move
             (and a late product re-categorisation re-keys an existing fact).
Scenario 3 - Missing product reference: the line is quarantined, no broken FK.

``python -m src.cli demo-scenarios`` runs all batches against an isolated
``<DB_NAME>_demo`` database and writes ``reports/scenario_demo.txt``.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from src.config.settings import PROJECT_ROOT, get_settings
from src.loading.database import derive_database_url, ensure_database_exists, get_engine, init_db
from src.validation.schemas import ENTITY_SPECS

# ----------------------------------------------------------------------------- CSV helpers
def write_batch(directory: Path, customers: list[dict[str, Any]] | None = None,
                products: list[dict[str, Any]] | None = None,
                orders: list[dict[str, Any]] | None = None,
                order_items: list[dict[str, Any]] | None = None) -> Path:
    """Write the four CSV files (header always present; missing keys become empty)."""
    directory.mkdir(parents=True, exist_ok=True)
    data = {"customers": customers or [], "products": products or [], "orders": orders or [],
            "order_items": order_items or []}
    for entity, rows in data.items():
        spec = ENTITY_SPECS[entity]
        with (directory / spec.file_name).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(spec.columns)
            for row in rows:
                writer.writerow([row.get(c, "") for c in spec.columns])
    return directory


def customer(cid: str, city: str, state: str, updated_at: str, first: str = "Asha",
             last: str = "Reddy", created_at: str = "2023-01-01 09:00:00") -> dict[str, str]:
    return {"customer_id": cid, "first_name": first, "last_name": last,
            "email": f"{first.lower()}.{cid.lower()}@example.in", "city": city, "state": state,
            "country": "India", "created_at": created_at, "updated_at": updated_at}


def product(pid: str, category: str, updated_at: str, name: str = "Galaxy Phone",
            brand: str = "Samsung", price: str = "1000.00",
            created_at: str = "2023-01-01 09:00:00") -> dict[str, str]:
    return {"product_id": pid, "product_name": name, "category": category, "brand": brand,
            "unit_price": price, "created_at": created_at, "updated_at": updated_at}


def order(oid: str, cid: str, order_date: str, status: str = "DELIVERED",
          payment: str = "UPI") -> dict[str, str]:
    return {"order_id": oid, "customer_id": cid, "order_date": order_date,
            "order_status": status, "payment_method": payment, "updated_at": order_date}


def item(iid: str, oid: str, pid: str, qty: int, price: str, discount: str,
         created_at: str) -> dict[str, str]:
    return {"order_item_id": iid, "order_id": oid, "product_id": pid, "quantity": str(qty),
            "unit_price": price, "discount_amount": discount, "created_at": created_at}


def build_scenario_batches(base_dir: Path) -> dict[str, Path]:
    """Create the four scenario batches. Returns {batch_name: directory}."""
    batches: dict[str, Path] = {}
    c1_v1 = customer("C1", "Hyderabad", "Telangana", "2024-01-01 10:00:00")
    c1_v2 = customer("C1", "Chennai", "Tamil Nadu", "2024-06-01 00:00:00")
    c2 = customer("C2", "Bengaluru", "Karnataka", "2024-01-01 10:00:00", first="Ravi",
                  last="Kumar")
    p1_v1 = product("P1", "Electronics", "2024-01-01 10:00:00")
    p1_v2 = product("P1", "Mobiles", "2024-05-01 00:00:00")
    p2 = product("P2", "Books", "2024-01-01 10:00:00", name="Data Engineering Guide",
                 brand="Penguin", price="500.00")

    batches["batch1_initial"] = write_batch(
        base_dir / "batch1_initial", customers=[c1_v1, c2], products=[p1_v1, p2],
        orders=[order("O1", "C1", "2024-03-10 11:00:00"), order("O2", "C2", "2024-03-12 12:00:00")],
        order_items=[item("OI1", "O1", "P1", 2, "1000.00", "100.00", "2024-03-10 11:00:00"),
                     item("OI2", "O2", "P2", 1, "500.00", "0.00", "2024-03-12 12:00:00")])
    # Scenario 1: C1 moves on 2024-06-01; a July order follows.
    batches["batch2_customer_moves"] = write_batch(
        base_dir / "batch2_customer_moves", customers=[c1_v2],
        orders=[order("O3", "C1", "2024-07-15 18:30:00")],
        order_items=[item("OI3", "O3", "P1", 1, "1000.00", "50.00", "2024-07-15 18:30:00")])
    # Scenario 2: April order arrives late; P1 re-categorised effective 2024-05-01
    # (arrives late too, so OI3 - already loaded - must be re-keyed). OI3 is
    # re-delivered unchanged and must not be duplicated.
    batches["batch3_late_arrivals"] = write_batch(
        base_dir / "batch3_late_arrivals", products=[p1_v2],
        orders=[order("O4", "C1", "2024-04-20 09:15:00"), order("O3", "C1", "2024-07-15 18:30:00")],
        order_items=[item("OI4", "O4", "P1", 3, "950.00", "0.00", "2024-04-20 09:15:00"),
                     item("OI3", "O3", "P1", 1, "1000.00", "50.00", "2024-07-15 18:30:00")])
    # Scenario 3: unknown product reference.
    batches["batch4_missing_product"] = write_batch(
        base_dir / "batch4_missing_product",
        orders=[order("O5", "C2", "2024-08-01 10:00:00")],
        order_items=[item("OI5", "O5", "P999", 1, "250.00", "0.00", "2024-08-01 10:00:00"),
                     item("OI6", "O5", "P2", 2, "500.00", "20.00", "2024-08-01 10:00:00")])
    return batches


# ----------------------------------------------------------------------------- inspection
def fact_view(engine: Engine) -> pd.DataFrame:
    """Facts joined to the dimension versions they reference."""
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT f.order_item_id, f.order_id, f.date_key, f.customer_key, c.city,
                   f.product_key, p.category, f.quantity, f.gross_sales, f.discount_amount,
                   f.net_revenue
            FROM dw.fact_sales f
            JOIN dw.dim_customer c ON c.customer_key = f.customer_key
            JOIN dw.dim_product p ON p.product_key = f.product_key
            ORDER BY f.order_item_id"""))
        return pd.DataFrame(result.all(), columns=list(result.keys()))


def dimension_view(engine: Engine, table: str) -> pd.DataFrame:
    """All versions of a dimension (dim_customer / dim_product)."""
    queries = {
        "dim_customer": """SELECT customer_key, customer_id, city, state, effective_start_date,
                                  effective_end_date, is_current
                           FROM dw.dim_customer ORDER BY customer_id, effective_start_date""",
        "dim_product": """SELECT product_key, product_id, category, brand, effective_start_date,
                                 effective_end_date, is_current
                          FROM dw.dim_product ORDER BY product_id, effective_start_date""",
    }
    with engine.connect() as conn:
        result = conn.execute(text(queries[table]))
        return pd.DataFrame(result.all(), columns=list(result.keys()))


def run_scenario_demo(output: Path | None = None) -> Path:
    """Run all scenario batches in the isolated demo database and write a report."""
    from src.pipeline import run_pipeline

    settings = get_settings()
    demo_url = derive_database_url(settings.database_url, "_demo")
    ensure_database_exists(demo_url)
    engine = get_engine(demo_url)
    output = output or settings.reports_dir / "scenario_demo.txt"
    work = PROJECT_ROOT / "data" / "generated" / "scenarios"
    lines: list[str] = ["Scenario demonstration (isolated database: "
                        f"{demo_url.rsplit('/', 1)[-1]})", "=" * 78]
    try:
        init_db(engine, reset=True)
        batches = build_scenario_batches(work)
        for name, directory in batches.items():
            result = run_pipeline(directory, engine=engine, settings=settings,
                                  reports_dir=settings.reports_dir / "scenarios")
            c = result.summary.counts
            lines += ["", f"### {name}: status={result.status} input={c.input_count} "
                          f"valid={c.valid_count} rejected={c.rejected_count} "
                          f"facts inserted={c.inserted_count} updated={c.updated_count} "
                          f"unchanged={c.unchanged_count} "
                          f"re-keyed={int(result.metrics.get('facts_rekeyed', 0))}"]
            if name == "batch4_missing_product":
                with engine.connect() as conn:
                    rejects = conn.execute(text("""
                        SELECT entity_name, source_record_id, rejection_reason
                        FROM ops.rejected_records WHERE run_id = :r"""),
                                           {"r": result.run_id}).all()
                lines += [f"  quarantined: {r.entity_name} {r.source_record_id}: "
                          f"{r.rejection_reason}" for r in rejects]
        with pd.option_context("display.width", 200, "display.max_columns", 20):
            lines += ["", "dim_customer (SCD2):", dimension_view(engine, "dim_customer").to_string(index=False),
                      "", "dim_product (SCD2):", dimension_view(engine, "dim_product").to_string(index=False),
                      "", "fact_sales with the dimension versions they reference:",
                      fact_view(engine).to_string(index=False)]
        lines += ["", "Expected:",
                  " * C1 has two versions; OI1 (March) and OI4 (April, late) -> Hyderabad key,",
                  "   OI3 (July) -> Chennai key.",
                  " * P1 has two versions; OI1/OI4 (before 2024-05-01) -> Electronics,",
                  "   OI3 (July) moves to the Mobiles version when the late product change",
                  "   arrives (in-place fact update - no new fact row).",
                  " * 7 order lines delivered over 4 batches (OI3 twice, OI5 invalid) -> 5 facts.",
                  " * OI5 (unknown product P999) is quarantined; no fact row, no broken FK."]
    finally:
        engine.dispose()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output
