"""Read-side queries behind the API: dashboard, quality, warehouse catalog,
analytics, reports and exports.

All SQL uses bound parameters. Table identifiers are only ever taken from the
fixed ``WAREHOUSE_TABLES`` whitelist, never from user input directly.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text

from app.database.models import TABLE_CATALOG
from app.services.analytics.queries import load_queries, run_query
from app.services.reconciliation.reconcile import reconcile_run
from app.services.validation.schemas import ReasonCode

RECOGNISED = "order_status NOT IN ('CANCELLED', 'RETURNED')"
RAW_TABLES = ("customers", "products", "orders", "order_items")

MISSING_CODES = {ReasonCode.MISSING_REQUIRED_FIELD}
DUPLICATE_CODES = {ReasonCode.EXACT_DUPLICATE, ReasonCode.CONFLICTING_DUPLICATE,
                   ReasonCode.DUPLICATE_EMAIL}
REFERENCE_CODES = {ReasonCode.UNKNOWN_CUSTOMER_REFERENCE, ReasonCode.UNKNOWN_ORDER_REFERENCE,
                   ReasonCode.UNKNOWN_PRODUCT_REFERENCE}
SCHEMA_CODES = {ReasonCode.INVALID_EMAIL, ReasonCode.INVALID_TIMESTAMP, ReasonCode.INVALID_NUMBER,
                ReasonCode.INVALID_DECIMAL_SCALE, ReasonCode.VALUE_OUT_OF_RANGE,
                ReasonCode.VALUE_TOO_LONG, ReasonCode.INVALID_ALLOWED_VALUE,
                ReasonCode.INVALID_FORMAT, ReasonCode.MALFORMED_ROW}
BUSINESS_CODES = {ReasonCode.NEGATIVE_VALUE, ReasonCode.NON_POSITIVE_QUANTITY,
                  ReasonCode.EXCESSIVE_DISCOUNT, ReasonCode.TIMESTAMP_ORDER}

# name -> (schema, kind, description), from the model catalog
WAREHOUSE_TABLES: dict[str, tuple[str, str, str]] = {
    t.name: (t.schema, t.kind, t.description) for t in TABLE_CATALOG}


# ----------------------------------------------------------------------------- helpers
def plain(value: Any) -> Any:
    """JSON-friendly value (Decimal -> float, datetime -> ISO string)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value


def _rows(engine: Engine, sql: str, **params: Any) -> list[dict[str, Any]]:
    with engine.connect() as conn:
        return [dict(r._mapping) for r in conn.execute(text(sql), params)]


def _one(engine: Engine, sql: str, **params: Any) -> dict[str, Any]:
    rows = _rows(engine, sql, **params)
    return rows[0] if rows else {}


def _records(frame) -> list[dict[str, Any]]:
    return [{k: plain(v) for k, v in row.items()} for row in frame.to_dict("records")]


RUN_COLUMNS = """run_id, status, source_file AS source, start_time, end_time,
                 execution_duration_seconds AS duration_seconds, input_count, valid_count,
                 rejected_count, inserted_count, updated_count, failed_count, error_message"""


def pipeline_runs(engine: Engine, limit: int = 50) -> list[dict[str, Any]]:
    return [plain(r) for r in _rows(engine, f"""
        SELECT {RUN_COLUMNS} FROM ops.pipeline_runs ORDER BY start_time DESC LIMIT :n""", n=limit)]


def pipeline_run(engine: Engine, run_id: str) -> dict[str, Any] | None:
    row = _one(engine, f"SELECT {RUN_COLUMNS} FROM ops.pipeline_runs WHERE run_id = :r", r=run_id)
    return plain(row) if row else None


def run_stage_seconds(engine: Engine, run_id: str) -> dict[str, float]:
    rows = _rows(engine, """SELECT metric_name, metric_value FROM ops.pipeline_metrics
                            WHERE run_id = :r AND metric_name LIKE '%\\_seconds' ESCAPE '\\'""",
                 r=run_id)
    return {r["metric_name"].removesuffix("_seconds"): float(r["metric_value"]) for r in rows}


def raw_status_counts(engine: Engine) -> dict[str, int]:
    union = " UNION ALL ".join(
        f"SELECT validation_status, count(*) AS n FROM raw.{t} GROUP BY validation_status"
        for t in RAW_TABLES)
    rows = _rows(engine, f"SELECT validation_status, sum(n) AS n FROM ({union}) x GROUP BY 1")
    return {r["validation_status"]: int(r["n"]) for r in rows}


def rejections_by_reason(engine: Engine) -> list[dict[str, Any]]:
    return [{"name": r["code"], "value": int(r["n"])} for r in _rows(engine, """
        SELECT code, count(*) AS n FROM ops.rejected_records, unnest(reason_codes) AS code
        GROUP BY code ORDER BY n DESC, code""")]


def monthly_sales(engine: Engine) -> list[dict[str, Any]]:
    rows = _records(run_query(engine, "monthly_revenue"))
    for r in rows:
        r["period"] = f"{r['month_name'][:3]} {r['year']}"
    return rows


# ----------------------------------------------------------------------------- dashboard
def dashboard_summary(engine: Engine) -> dict[str, Any]:
    counts = raw_status_counts(engine)
    total = sum(counts.values())
    valid = counts.get("VALID", 0)
    rejected = int(_one(engine, "SELECT count(*) AS n FROM ops.rejected_records")["n"])
    sales = _one(engine, f"""SELECT COALESCE(sum(net_revenue), 0) AS revenue,
                                    count(DISTINCT order_id) AS orders
                             FROM dw.fact_sales WHERE {RECOGNISED}""")
    runs = _one(engine, """SELECT count(*) AS total,
                                  count(*) FILTER (WHERE status = 'SUCCESS') AS ok,
                                  count(*) FILTER (WHERE status <> 'RUNNING') AS finished
                           FROM ops.pipeline_runs""")
    recent = pipeline_runs(engine, 5)
    queries = load_queries()
    return {
        "total_source_records": total,
        "processed_records": valid,
        "rejected_records": rejected,
        "total_revenue": float(sales["revenue"]),
        "total_orders": int(sales["orders"]),
        "pipeline_success_rate": (round(100.0 * runs["ok"] / runs["finished"], 1)
                                  if runs["finished"] else None),
        "total_runs": int(runs["total"]),
        "latest_run": recent[0] if recent else None,
        "recent_runs": recent,
        "revenue_trend": monthly_sales(engine),
        "category_sales": _records(run_query(engine, "category_revenue", queries)),
        "top_products": _records(run_query(engine, "top_10_products", queries))[:5],
        "quality": {"valid": valid, "rejected": rejected, "by_reason": rejections_by_reason(engine)},
    }


# ----------------------------------------------------------------------------- quality
def quality_summary(engine: Engine) -> dict[str, Any]:
    counts = raw_status_counts(engine)
    records = _rows(engine, "SELECT reason_codes FROM ops.rejected_records")

    def matching(codes: set[str]) -> int:
        return sum(1 for r in records if codes.intersection(r["reason_codes"]))

    latest = _one(engine, "SELECT run_id FROM ops.pipeline_runs ORDER BY start_time DESC LIMIT 1")
    by_entity = [{"name": r["entity_name"], "value": int(r["n"])} for r in _rows(engine, """
        SELECT entity_name, count(*) AS n FROM ops.rejected_records
        GROUP BY entity_name ORDER BY entity_name""")]
    return {
        "run_id": latest.get("run_id"),
        "total_records": sum(counts.values()),
        "valid_records": counts.get("VALID", 0),
        "rejected_records": len(records),
        "missing_values": matching(MISSING_CODES),
        "duplicate_records": matching(DUPLICATE_CODES),
        "invalid_references": matching(REFERENCE_CODES),
        "schema_errors": matching(SCHEMA_CODES),
        "business_rule_errors": matching(BUSINESS_CODES),
        "by_reason": rejections_by_reason(engine),
        "by_entity": by_entity,
    }


def rejections(engine: Engine, entity: str | None, reason: str | None, run_id: str | None,
               limit: int, offset: int) -> dict[str, Any]:
    where = """WHERE (CAST(:entity AS text) IS NULL OR entity_name = :entity)
                 AND (CAST(:reason AS text) IS NULL OR CAST(:reason AS text) = ANY(reason_codes))
                 AND (CAST(:run AS text) IS NULL OR run_id = :run)"""
    params = {"entity": entity, "reason": reason, "run": run_id}
    total = int(_one(engine, f"SELECT count(*) AS n FROM ops.rejected_records {where}",
                     **params)["n"])
    items = _rows(engine, f"""
        SELECT reject_id, run_id, entity_name, source_file, source_row_number, source_record_id,
               reason_codes, rejection_reason, raw_record, rejected_at
        FROM ops.rejected_records {where}
        ORDER BY rejected_at DESC, entity_name, source_row_number
        LIMIT :limit OFFSET :offset""", **params, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "items": [plain(i) for i in items]}


def write_quality_report(engine: Engine, reports_dir: Path) -> Path:
    """reports/data_quality_report.csv - summary, by reason and by entity."""
    summary = quality_summary(engine)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "data_quality_report.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["section", "name", "value"])
        for key in ("total_records", "valid_records", "rejected_records", "missing_values",
                    "duplicate_records", "invalid_references", "schema_errors",
                    "business_rule_errors"):
            writer.writerow(["summary", key, summary[key]])
        for item in summary["by_reason"]:
            writer.writerow(["reason_code", item["name"], int(item["value"])])
        for item in summary["by_entity"]:
            writer.writerow(["entity", item["name"], int(item["value"])])
    return path


# ----------------------------------------------------------------------------- warehouse
def warehouse_tables(engine: Engine) -> list[dict[str, Any]]:
    out = []
    with engine.connect() as conn:
        for name, (schema, kind, description) in WAREHOUSE_TABLES.items():
            count = conn.execute(text(f'SELECT count(*) FROM "{schema}"."{name}"')).scalar_one()
            out.append({"schema": schema, "name": name, "kind": kind,
                        "description": description, "row_count": int(count)})
    return out


def warehouse_table(engine: Engine, name: str, limit: int) -> dict[str, Any] | None:
    if name not in WAREHOUSE_TABLES:
        return None
    schema, kind, description = WAREHOUSE_TABLES[name]
    columns = _rows(engine, """
        SELECT c.column_name AS name,
               CASE WHEN c.data_type IN ('character varying', 'character')
                         THEN c.data_type || '(' || c.character_maximum_length || ')'
                    WHEN c.data_type = 'numeric' AND c.numeric_precision IS NOT NULL
                         THEN 'numeric(' || c.numeric_precision || ',' || c.numeric_scale || ')'
                    ELSE c.data_type END AS data_type,
               c.is_nullable = 'YES' AS nullable
        FROM information_schema.columns c
        WHERE c.table_schema = :s AND c.table_name = :t ORDER BY c.ordinal_position""",
                    s=schema, t=name)
    pk = {r["column_name"] for r in _rows(engine, """
        SELECT kcu.column_name FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu
          ON kcu.constraint_name = tc.constraint_name AND kcu.table_schema = tc.table_schema
        WHERE tc.table_schema = :s AND tc.table_name = :t AND tc.constraint_type = 'PRIMARY KEY'""",
                                          s=schema, t=name)}
    fks = {r["column_name"]: r["target"] for r in _rows(engine, """
        SELECT a.attname AS column_name,
               nf.nspname || '.' || cf.relname || '(' || af.attname || ')' AS target
        FROM pg_constraint con
        JOIN pg_class c ON c.oid = con.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_class cf ON cf.oid = con.confrelid JOIN pg_namespace nf ON nf.oid = cf.relnamespace
        JOIN LATERAL unnest(con.conkey, con.confkey) AS k(col, fcol) ON TRUE
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.col
        JOIN pg_attribute af ON af.attrelid = cf.oid AND af.attnum = k.fcol
        WHERE con.contype = 'f' AND n.nspname = :s AND c.relname = :t""", s=schema, t=name)}
    order = "sales_key" if name == "fact_sales" else None
    order_sql = f" ORDER BY {order}" if order else ""
    with engine.connect() as conn:
        count = conn.execute(text(f'SELECT count(*) FROM "{schema}"."{name}"')).scalar_one()
        sample = [plain(dict(r._mapping)) for r in conn.execute(
            text(f'SELECT * FROM "{schema}"."{name}"{order_sql} LIMIT :n'), {"n": limit})]
    return {
        "schema": schema, "name": name, "kind": kind, "description": description,
        "row_count": int(count),
        "columns": [{**c, "is_primary_key": c["name"] in pk, "foreign_key": fks.get(c["name"])}
                    for c in columns],
        "sample_rows": sample,
    }


# ----------------------------------------------------------------------------- analytics
def revenue_overview(engine: Engine) -> dict[str, Any]:
    queries = load_queries()
    total = _records(run_query(engine, "total_revenue", queries))[0]
    aov = _records(run_query(engine, "average_order_value", queries))[0]
    return {
        "total_revenue": float(total["net_revenue"] or 0),
        "gross_sales": float(total["gross_sales"] or 0),
        "discounts": float(total["discounts"] or 0),
        "orders": int(total["orders"] or 0),
        "order_lines": int(total["order_lines"] or 0),
        "units_sold": int(total["units_sold"] or 0),
        "average_order_value": aov["average_order_value"],
        "yearly": _records(run_query(engine, "yearly_revenue", queries)),
        "order_status": _records(run_query(engine, "order_status_analysis", queries)),
        "cities": _records(run_query(engine, "city_revenue", queries)),
        "payment_methods": _records(run_query(engine, "revenue_by_payment_method", queries)),
    }


def top_products(engine: Engine, limit: int) -> list[dict[str, Any]]:
    return _records(run_query(engine, "top_10_products"))[:limit]


def top_customers(engine: Engine, limit: int) -> list[dict[str, Any]]:
    return _records(run_query(engine, "top_10_customers"))[:limit]


def category_sales(engine: Engine) -> list[dict[str, Any]]:
    return _records(run_query(engine, "category_revenue"))


# ----------------------------------------------------------------------------- reports
def latest_reconcilable_run(engine: Engine) -> str | None:
    row = _one(engine, """SELECT run_id FROM ops.pipeline_runs
                          WHERE status IN ('SUCCESS', 'RECONCILIATION_FAILED')
                          ORDER BY start_time DESC LIMIT 1""")
    return row.get("run_id")


def reconciliation_report(engine: Engine, run_id: str | None, source_system: str) -> dict[str, Any]:
    run_id = run_id or latest_reconcilable_run(engine)
    if run_id is None:
        return {"run_id": None, "passed": None, "checks": []}
    result = reconcile_run(engine, run_id, reports_dir=None, source_system=source_system)
    return {"run_id": run_id, "passed": result.passed, "checks": [
        {"check_name": c.check_name, "severity": c.severity, "status": c.status,
         "source_value": str(c.source_value), "target_value": str(c.target_value),
         "difference": str(c.difference), "explanation": c.explanation}
        for c in result.checks]}


def performance_report(engine: Engine, reports_dir: Path) -> dict[str, Any]:
    path = reports_dir / "performance_results.json"
    bench: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    runs = _rows(engine, """
        SELECT r.run_id, r.execution_duration_seconds AS seconds,
               COALESCE((SELECT metric_value FROM ops.pipeline_metrics m
                         WHERE m.run_id = r.run_id AND m.metric_name = 'order_items_input'), 0)
                   AS order_items
        FROM ops.pipeline_runs r WHERE r.status = 'SUCCESS'
        ORDER BY r.start_time DESC LIMIT 10""")
    latest = []
    for r in runs:
        seconds = float(r["seconds"] or 0)
        items = int(r["order_items"] or 0)
        latest.append({"run_id": r["run_id"], "order_items": items, "seconds": seconds,
                       "rows_per_second": round(items / seconds, 1) if seconds else 0.0,
                       "stages": run_stage_seconds(engine, r["run_id"])})
    return {"available": bool(bench.get("rows")), "generated_at": bench.get("generated_at"),
            "postgres": bench.get("postgres"), "rows": bench.get("rows", []),
            "experiment": bench.get("experiment"), "latest_runs": latest}


def export_rows(engine: Engine, report: str, reports_dir: Path,
                source_system: str) -> list[dict[str, Any]]:
    """Flat rows for CSV/JSON export of one report."""
    if report == "reconciliation":
        rec = reconciliation_report(engine, None, source_system)
        return [{"run_id": rec["run_id"], **c} for c in rec["checks"]]
    if report == "quality":
        q = quality_summary(engine)
        rows = [{"section": "summary", "name": k, "value": q[k]} for k in (
            "total_records", "valid_records", "rejected_records", "missing_values",
            "duplicate_records", "invalid_references", "schema_errors", "business_rule_errors")]
        rows += [{"section": "reason_code", "name": r["name"], "value": int(r["value"])}
                 for r in q["by_reason"]]
        rows += [{"section": "entity", "name": r["name"], "value": int(r["value"])}
                 for r in q["by_entity"]]
        return rows
    if report == "rejections":
        items = _rows(engine, """
            SELECT reject_id, run_id, entity_name, source_file, source_row_number,
                   source_record_id, array_to_string(reason_codes, '|') AS reason_codes,
                   rejection_reason, raw_record::text AS raw_record, rejected_at
            FROM ops.rejected_records ORDER BY reject_id""")
        return [plain(i) for i in items]
    if report == "performance":
        perf = performance_report(engine, reports_dir)
        rows = [{"kind": "benchmark", **r} for r in perf["rows"]]
        rows += [{"kind": "pipeline_run", "run_id": r["run_id"], "order_items": r["order_items"],
                  "total_s": r["seconds"], "rows_per_second": r["rows_per_second"]}
                 for r in perf["latest_runs"]]
        return rows
    if report == "history":
        return pipeline_runs(engine, 10_000)
    raise ValueError(f"unknown report '{report}'")


def to_csv(rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    if not rows:
        return ""
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    writer = csv.DictWriter(buffer, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: plain(v) for k, v in row.items()})
    return buffer.getvalue()
