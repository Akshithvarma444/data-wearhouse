"""Static HTML dashboard of the warehouse: KPI tiles, charts and tables.

    python -m src.cli dashboard            # writes reports/dashboard.html
    python -m src.cli dashboard --open     # ...and opens it in the browser

The page is a single self-contained file (data embedded as JSON, charts drawn as
inline SVG by a small script - no CDN, no extra Python packages), so it works
offline and can be e-mailed or attached as is. It is a snapshot: re-run the
command after a pipeline run to refresh it.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import Engine, text

from src.analytics import load_queries, run_query
from src.loading.database import database_name
from src.monitoring.logger import get_logger

log = get_logger("dashboard")

RECOGNISED = "f.order_status NOT IN ('CANCELLED', 'RETURNED')"


def _plain(value: Any) -> Any:
    """Make query values JSON-serialisable (Decimal -> float for display only)."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat(sep=" ", timespec="seconds") if isinstance(value, datetime) \
            else value.isoformat()
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if hasattr(value, "item"):  # numpy scalars
        return value.item()
    return value


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return [{k: _plain(v) for k, v in row.items()} for row in frame.to_dict("records")]


def _sql(engine: Engine, sql: str, **params: Any) -> pd.DataFrame:
    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        return pd.DataFrame(result.all(), columns=list(result.keys()))


def collect_history(demo_engine: Engine) -> dict[str, Any] | None:
    """SCD2 / late-arriving scenario results from the isolated demo database."""
    try:
        runs = _sql(demo_engine, """SELECT run_id, status, input_count, rejected_count,
                                           inserted_count, updated_count
                                    FROM ops.pipeline_runs ORDER BY start_time""")
    except Exception as exc:  # demo database or its schema does not exist yet
        log.info("No scenario data for the dashboard (%s)", type(exc).__name__)
        return None
    if runs.empty:
        return None
    return {
        "database": database_name(demo_engine.url.render_as_string(hide_password=False)),
        "runs": _records(runs),
        "customers": _records(_sql(demo_engine, """
            SELECT customer_key, customer_id, city, state, effective_start_date,
                   effective_end_date, is_current
            FROM dw.dim_customer ORDER BY customer_id, effective_start_date""")),
        "products": _records(_sql(demo_engine, """
            SELECT product_key, product_id, category, brand, effective_start_date,
                   effective_end_date, is_current
            FROM dw.dim_product ORDER BY product_id, effective_start_date""")),
        "facts": _records(_sql(demo_engine, """
            SELECT f.order_item_id, f.order_id, f.date_key, f.customer_key, c.city,
                   f.product_key, p.category, f.quantity, f.net_revenue
            FROM dw.fact_sales f JOIN dw.dim_customer c USING (customer_key)
            JOIN dw.dim_product p USING (product_key) ORDER BY f.order_item_id""")),
        "rejects": _records(_sql(demo_engine, """
            SELECT source_record_id, array_to_string(reason_codes, ', ') AS reason_codes,
                   rejection_reason FROM ops.rejected_records ORDER BY reject_id""")),
    }


def collect_dashboard_data(engine: Engine, reports_dir: Path,
                           demo_engine: Engine | None = None) -> dict[str, Any]:
    """Query everything the dashboard shows."""
    queries = load_queries()
    q = lambda name: _records(run_query(engine, name, queries))  # noqa: E731

    kpi = _sql(engine, f"""
        SELECT (SELECT count(*) FROM dw.fact_sales)                          AS fact_rows,
               (SELECT COALESCE(sum(net_revenue), 0) FROM dw.fact_sales f WHERE {RECOGNISED}) AS net_revenue,
               (SELECT count(DISTINCT order_id) FROM dw.fact_sales f WHERE {RECOGNISED}) AS orders,
               (SELECT COALESCE(sum(quantity), 0) FROM dw.fact_sales f WHERE {RECOGNISED}) AS units,
               (SELECT count(*) FROM dw.dim_customer WHERE is_current)        AS customers,
               (SELECT count(*) FROM dw.dim_product WHERE is_current)         AS products,
               (SELECT count(*) FROM ops.rejected_records)                    AS rejected,
               (SELECT count(*) FROM ops.pipeline_runs)                       AS runs,
               (SELECT count(*) FROM ops.pipeline_runs WHERE status = 'SUCCESS') AS runs_ok
    """)
    kpis = _records(kpi)[0]
    aov = q("average_order_value")
    kpis["aov"] = aov[0]["average_order_value"] if aov else None

    runs = _sql(engine, """
        SELECT run_id, status, start_time, execution_duration_seconds AS seconds,
               input_count, valid_count, rejected_count, inserted_count, updated_count,
               COALESCE(error_message, '') AS error_message
        FROM ops.pipeline_runs ORDER BY start_time DESC LIMIT 25""")
    reasons = _sql(engine, """
        SELECT code AS reason_code, count(*) AS records
        FROM ops.rejected_records, unnest(reason_codes) AS code
        GROUP BY code ORDER BY records DESC, code""")
    rejects = _sql(engine, """
        SELECT entity_name, source_file, source_row_number AS row_no,
               COALESCE(source_record_id, '(blank)') AS record_id,
               array_to_string(reason_codes, ', ') AS reason_codes, rejection_reason
        FROM ops.rejected_records ORDER BY rejected_at DESC, entity_name, source_row_number
        LIMIT 100""")
    entity_quality = _sql(engine, """
        SELECT entity_name AS entity, count(*) AS rejected
        FROM ops.rejected_records GROUP BY entity_name ORDER BY entity_name""")

    reconciliation: list[dict[str, Any]] = []
    recon_file = reports_dir / "reconciliation_report.csv"
    if recon_file.exists():
        reconciliation = _records(pd.read_csv(recon_file, dtype=str, keep_default_na=False))

    performance = None
    perf_file = reports_dir / "performance_results.json"
    if perf_file.exists():
        performance = json.loads(perf_file.read_text(encoding="utf-8"))

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "database": database_name(engine.url.render_as_string(hide_password=False)),
        "kpis": kpis,
        "monthly": q("monthly_revenue"),
        "yearly": q("yearly_revenue"),
        "category": q("category_revenue"),
        "city": q("city_revenue"),
        "payment": q("revenue_by_payment_method"),
        "status": q("order_status_analysis"),
        "top_products": q("top_10_products"),
        "top_customers": q("top_10_customers"),
        "segments": q("customer_purchase_behaviour"),
        "scd_history": q("historical_customer_sales"),
        "runs": _records(runs),
        "reasons": _records(reasons),
        "rejects": _records(rejects),
        "entity_quality": _records(entity_quality),
        "reconciliation": reconciliation,
        "performance": performance,
        "history": collect_history(demo_engine) if demo_engine is not None else None,
    }


def build_dashboard(engine: Engine, reports_dir: Path, output: Path | None = None,
                    demo_engine: Engine | None = None) -> Path:
    """Write the dashboard HTML and a body-only fragment copy; return the page path."""
    output = output or reports_dir / "dashboard.html"
    data = collect_dashboard_data(engine, reports_dir, demo_engine)
    payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = _TEMPLATE.replace("__DATA__", payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(page, encoding="utf-8")
    # Same page without <html>/<head>/<body> wrappers, for hosts that add their own skeleton.
    fragment = page[page.index("<title>"):]
    fragment = fragment.replace("</head>\n<body>\n", "\n").replace("</body>\n</html>\n", "")
    output.with_name(output.stem + "_fragment.html").write_text(fragment, encoding="utf-8")
    return output


_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sales Warehouse Dashboard</title>
<meta name="claude:viewport-fit-sides" content="cover">
<style>
/* Same design language as the React app: navy sidebar, white cards, blue accent. Light theme by design. */
:root {
  color-scheme: light;
  --page: #f4f7fb; --surface: #ffffff; --ink: #0b1b35; --ink-2: #475569; --muted: #64748b;
  --grid: #e2e8f0; --axis: #cbd5e1; --border: rgba(15,23,42,0.08);
  --navy: #0b1b35; --navy-2: #050d1c; --brand: #2a78d6; --brand-2: #1f63b8; --brand-soft: #eef5ff;
  --series-1: #2a78d6; --series-2: #eb6834;
  --good: #10b981; --critical: #f43f5e; --good-text: #047857; --critical-text: #be123c;
  --hover: rgba(42,120,214,0.07);
  --shadow: 0 1px 2px rgba(15,23,42,0.04), 0 4px 16px -4px rgba(15,23,42,0.08);
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body { margin: 0; color: var(--ink); font: 14px/1.5 "Segoe UI Variable Text", "Segoe UI", system-ui, -apple-system, sans-serif;
       background: radial-gradient(1200px 500px at 85% -10%, rgba(42,120,214,0.07), transparent 60%), var(--page); }
.app { display: flex; min-height: 100%; }
aside { width: 256px; flex: none; background: linear-gradient(180deg, var(--navy), var(--navy-2)); color: #fff;
        display: flex; flex-direction: column; position: sticky; top: 0; height: 100vh; }
.brand { display: flex; align-items: center; gap: 12px; padding: 24px 20px; }
.brand .logo { width: 40px; height: 40px; border-radius: 12px; display: grid; place-items: center;
               background: linear-gradient(135deg, #5b9cf0, #1f63b8); box-shadow: 0 8px 20px -6px rgba(31,99,184,.6); }
.brand b { display: block; font-size: 15px; letter-spacing: -0.01em; }
.brand small { color: #94a3b8; font-size: 11px; }
.navlabel { color: #64748b; font-size: 10px; font-weight: 700; letter-spacing: .14em; text-transform: uppercase; padding: 0 24px 6px; }
nav { display: flex; flex-direction: column; gap: 2px; padding: 0 12px; }
nav button { position: relative; display: flex; align-items: center; gap: 12px; width: 100%; text-align: left; background: none; border: 0;
             color: #cbd5e1; font: inherit; font-size: 13.5px; padding: 9px 12px; border-radius: 12px; cursor: pointer; }
nav button svg { width: 18px; height: 18px; color: #94a3b8; flex: none; }
nav button:hover { background: rgba(255,255,255,0.05); color: #fff; }
nav button[aria-selected="true"] { background: linear-gradient(90deg, rgba(42,120,214,.28), rgba(42,120,214,.05)); color: #fff; font-weight: 600; }
nav button[aria-selected="true"] svg { color: #5b9cf0; }
nav button[aria-selected="true"]::before { content: ""; position: absolute; left: 0; top: 50%; width: 4px; height: 20px; transform: translateY(-50%);
                                           border-radius: 0 4px 4px 0; background: #5b9cf0; }
.status { margin: auto 12px 12px; padding: 14px; border-radius: 12px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); }
.status p { margin: 0; font-size: 12px; font-weight: 600; display: flex; align-items: center; gap: 8px; }
.status p i { width: 9px; height: 9px; border-radius: 50%; background: #34d399; display: inline-block; }
.status small { color: #94a3b8; font-size: 11px; display: block; margin-top: 4px; }
.content { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.topbar { position: sticky; top: 0; z-index: 5; display: flex; align-items: center; justify-content: space-between; gap: 12px;
          padding: 12px max(32px, env(safe-area-inset-right, 0px)) 12px max(32px, env(safe-area-inset-left, 0px));
          background: rgba(255,255,255,0.88); backdrop-filter: blur(8px); border-bottom: 1px solid var(--grid); }
.topbar .t { display: flex; align-items: center; gap: 10px; min-width: 0; }
.topbar .ic { width: 32px; height: 32px; border-radius: 10px; background: var(--brand-soft); color: var(--brand); display: grid; place-items: center; }
.topbar .ic svg { width: 16px; height: 16px; }
.topbar b { font-size: 14px; display: block; }
.topbar small { color: var(--muted); font-size: 12px; }
.pill { display: inline-flex; align-items: center; gap: 8px; border: 1px solid #a7f3d0; background: #ecfdf5; color: var(--good-text);
        border-radius: 999px; padding: 4px 12px; font-size: 12px; font-weight: 600; white-space: nowrap; }
.pill i { width: 8px; height: 8px; border-radius: 50%; background: var(--good); }
.menu { display: none; background: none; border: 0; padding: 6px; border-radius: 8px; cursor: pointer; color: var(--ink-2); }
main { padding-block: 28px 48px; padding-left: max(32px, env(safe-area-inset-left, 0px)); padding-right: max(32px, env(safe-area-inset-right, 0px));
       width: 100%; max-width: 1360px; margin: 0 auto; }
section[hidden] { display: none; }
.eyebrow { color: var(--brand); font-size: 12px; font-weight: 700; letter-spacing: .1em; text-transform: uppercase; margin: 0 0 4px; }
.pagetitle { font-size: 24px; font-weight: 700; letter-spacing: -0.02em; margin: 0 0 22px; }
.hero { position: relative; overflow: hidden; border-radius: 24px; padding: 30px; margin-bottom: 20px; color: #fff;
        background: linear-gradient(135deg, #0b1b35, #12284d 55%, #1c5096); box-shadow: 0 12px 32px -8px rgba(15,23,42,.25);
        display: grid; grid-template-columns: minmax(0,1.4fr) minmax(0,1fr); gap: 28px; }
.hero::after { content: ""; position: absolute; right: -90px; top: -90px; width: 280px; height: 280px; border-radius: 50%;
               background: rgba(91,156,240,.22); filter: blur(50px); pointer-events: none; }
.hero .k { color: #bcd6fe; font-size: 12px; font-weight: 700; letter-spacing: .16em; text-transform: uppercase; margin: 0; }
.hero .big { font-size: 46px; font-weight: 800; letter-spacing: -0.03em; margin: 6px 0 6px; font-variant-numeric: tabular-nums; }
.hero .s { color: #cbd5e1; margin: 0; }
.hero .panel { position: relative; z-index: 1; border-radius: 16px; padding: 18px; background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.15); }
.hero .panel dl { display: grid; grid-template-columns: 1fr 1fr; gap: 12px 16px; margin: 12px 0 0; }
.hero .panel dt { color: #cbd5e1; font-size: 12px; } .hero .panel dd { margin: 0; font-weight: 600; font-variant-numeric: tabular-nums; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr)); gap: 16px; margin-bottom: 20px; }
.tile { background: var(--surface); border: 1px solid var(--border); border-radius: 18px; padding: 18px 20px; box-shadow: var(--shadow); }
.tile .row { display: flex; justify-content: space-between; align-items: flex-start; gap: 10px; }
.tile .label { color: var(--muted); font-size: 13px; font-weight: 500; }
.tile .chip { width: 36px; height: 36px; border-radius: 12px; display: grid; place-items: center; color: #fff; flex: none; }
.tile .chip svg { width: 18px; height: 18px; }
.tile .value { font-size: 26px; font-weight: 700; letter-spacing: -0.02em; margin-top: 6px; font-variant-numeric: tabular-nums; }
.tile .note { color: var(--muted); font-size: 12px; margin-top: 4px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(min(460px, 100%), 1fr)); gap: 20px; }
.card { background: var(--surface); border: 1px solid var(--border); border-radius: 18px; padding: 20px; min-width: 0; box-shadow: var(--shadow); }
.card.wide { grid-column: 1 / -1; }
.card-head { display: flex; justify-content: space-between; align-items: center; gap: 8px; margin-bottom: 4px; }
.card h2 { font-size: 15px; margin: 0; font-weight: 650; letter-spacing: -0.01em; }
.card .desc { color: var(--muted); font-size: 12px; margin: 0 0 12px; }
.toggle { display: inline-flex; border: 1px solid var(--grid); border-radius: 10px; overflow: hidden; flex: none; background: #f8fafc; }
.toggle button { background: none; border: 0; color: var(--ink-2); font: inherit; font-size: 12px; padding: 5px 12px; cursor: pointer; }
.toggle button[aria-pressed="true"] { background: var(--brand); color: #fff; font-weight: 600; }
svg { display: block; width: 100%; height: auto; overflow: visible; }
svg text { fill: var(--ink-2); font-size: 12px; }
svg .tick { fill: var(--muted); font-variant-numeric: tabular-nums; }
.table-wrap { overflow-x: auto; max-height: 440px; overflow-y: auto; border: 1px solid var(--grid); border-radius: 12px; }
table { border-collapse: collapse; width: 100%; font-size: 13px; }
th, td { text-align: left; padding: 9px 12px; border-bottom: 1px solid #f1f5f9; white-space: nowrap; }
th { color: var(--muted); font-weight: 600; font-size: 11px; text-transform: uppercase; letter-spacing: .05em; position: sticky; top: 0; background: #f8fafc; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.wrap { white-space: normal; min-width: 260px; }
tbody tr:hover { background: var(--hover); }
.badge { display: inline-flex; align-items: center; gap: 5px; font-weight: 600; font-size: 12px; border-radius: 999px; padding: 2px 9px; }
.badge i { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
.ok { color: var(--good-text); background: #ecfdf5; } .ok i { background: var(--good); }
.bad { color: var(--critical-text); background: #fff1f2; } .bad i { background: var(--critical); }
.info { color: var(--ink-2); background: #f1f5f9; } .info i { background: var(--axis); }
.tip { position: fixed; pointer-events: none; background: #fff; color: var(--ink); border: 1px solid var(--border); border-radius: 10px;
       padding: 8px 11px; font-size: 12px; box-shadow: 0 12px 32px -8px rgba(15,23,42,.2); display: none; z-index: 20; max-width: 280px; }
.tip b { display: block; font-weight: 600; margin-bottom: 2px; }
.empty { color: var(--muted); padding: 24px 0; text-align: center; }

/* plain overrides: flat panels, no decorative gradients or icon badges */
body { background: #f3f4f6; font-family: "Segoe UI", system-ui, sans-serif; }
aside { background: #172338; width: 210px; }
.brand { padding: 16px; border-bottom: 1px solid rgba(255,255,255,.1); margin-bottom: 10px; }
.brand .logo { display: none; }
nav button { border-radius: 0; border-left: 2px solid transparent; padding: 7px 14px; font-size: 13px; }
nav button[aria-selected="true"] { background: rgba(255,255,255,.08); border-left-color: #5d8fd0; }
nav button[aria-selected="true"]::before { display: none; }
.status { border-radius: 4px; }
.topbar { background: #fff; backdrop-filter: none; padding-top: 10px; padding-bottom: 10px; }
.topbar .ic { display: none; }
.pill { border: 0; background: none; color: #64748b; font-weight: 400; }
.eyebrow { display: none; }
.pagetitle { font-size: 18px; font-weight: 600; margin-bottom: 14px; padding-bottom: 10px; border-bottom: 1px solid #e2e8f0; }
.hero { background: #fff; color: #0f172a; border: 1px solid #e2e8f0; border-radius: 6px; box-shadow: none; padding: 16px 18px; gap: 18px; }
.hero::after { display: none; }
.hero .k { color: #64748b; letter-spacing: 0; text-transform: none; font-weight: 500; font-size: 13px; }
.hero .big { font-size: 30px; font-weight: 600; letter-spacing: 0; margin: 2px 0; }
.hero .s { color: #475569; }
.hero .s b { color: #0f172a !important; }
.hero .panel { background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 4px; }
.hero .panel dt { color: #64748b; }
.tile { border-radius: 6px; box-shadow: none; padding: 12px 14px; }
.tile .chip { display: none; }
.tile .value { font-size: 20px; font-weight: 600; letter-spacing: 0; }
.card { border-radius: 6px; box-shadow: none; padding: 14px 16px; }
.toggle { border-radius: 4px; }
.toggle button[aria-pressed="true"] { background: #e2e8f0; color: #0f172a; }
.table-wrap { border-radius: 0; }
th { text-transform: none; letter-spacing: 0; font-size: 12px; color: #475569; background: #f1f5f9; }
.badge { border-radius: 3px; }
@media (max-width: 1000px) {
  aside { position: fixed; left: 0; top: 0; z-index: 30; transform: translateX(-100%); transition: transform .2s; }
  body.nav-open aside { transform: none; box-shadow: 0 0 0 100vmax rgba(5,13,28,.45); }
  .menu { display: inline-flex; }
  .hero { grid-template-columns: 1fr; }
}
@media (max-width: 640px) {
  main { padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
  .topbar { padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); }
  .hero { padding: 22px; } .hero .big { font-size: 36px; }
  .grid { grid-template-columns: 1fr; } .pill { display: none; }
}
@media (prefers-reduced-motion: reduce) { aside { transition: none; } }
</style>
</head>
<body>
<div class="app">
<aside>
  <div class="brand">
    <span class="logo"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/></svg></span>
    <span><b>Sales Warehouse</b><small>E-commerce data platform</small></span>
  </div>
  <p class="navlabel">Report</p>
  <nav role="tablist" id="tabs"></nav>
  <div class="status"><p><i></i>Snapshot report</p><small id="meta"></small></div>
</aside>
<div class="content">
  <div class="topbar">
    <div class="t">
      <button class="menu" id="menu" aria-label="Open menu"><svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 6h16M4 12h16M4 18h16"/></svg></button>
      <span class="ic" id="topicon"></span>
      <span><b id="toptitle"></b><small id="topdesc"></small></span>
    </div>
    <span class="pill"><i></i><span id="pilltext"></span></span>
  </div>
  <main>
    <section id="tab-sales"></section>
    <section id="tab-quality" hidden></section>
    <section id="tab-pipeline" hidden></section>
    <section id="tab-performance" hidden></section>
    <section id="tab-history" hidden></section>
  </main>
</div>
</div>
<div class="tip" id="tip"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('data').textContent);
const inr = new Intl.NumberFormat('en-IN', {style: 'currency', currency: 'INR', maximumFractionDigits: 0});
const inr2 = new Intl.NumberFormat('en-IN', {style: 'currency', currency: 'INR', minimumFractionDigits: 2, maximumFractionDigits: 2});
const num = new Intl.NumberFormat('en-IN');
const compact = v => { const a = Math.abs(v);
  if (a >= 1e7) return '₹' + (v / 1e7).toFixed(a >= 1e8 ? 0 : 1) + ' Cr';
  if (a >= 1e5) return '₹' + (v / 1e5).toFixed(a >= 1e6 ? 0 : 1) + ' L';
  if (a >= 1e3) return '₹' + (v / 1e3).toFixed(0) + 'K';
  return '₹' + Math.round(v); };
const NS = 'http://www.w3.org/2000/svg';
const el = (tag, attrs = {}, parent) => { const n = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v); if (parent) parent.appendChild(n); return n; };
const h = (tag, cls, html) => { const n = document.createElement(tag); if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html; return n; };
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const tip = document.getElementById('tip');
function showTip(evt, title, lines) {
  tip.innerHTML = '<b>' + esc(title) + '</b>' + lines.map(esc).join('<br>');
  tip.style.display = 'block';
  const x = Math.min(evt.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  const y = Math.min(evt.clientY + 14, window.innerHeight - tip.offsetHeight - 8);
  tip.style.left = x + 'px'; tip.style.top = y + 'px';
}
const hideTip = () => { tip.style.display = 'none'; };

function niceMax(v) { if (v <= 0) return 1; const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p; return 10 * p; }

/* Horizontal bar chart - one series, value labels at bar ends, hover tooltip. */
function barChart(items, opts) {
  const W = 620, rowH = 28, left = opts.labelWidth || 160, right = 76, top = 4;
  const H = top + items.length * rowH + 22;
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': opts.title});
  const max = niceMax(Math.max(...items.map(d => d.value), 0));
  const x = v => left + (W - left - right) * (v / max);
  for (let i = 0; i <= 4; i++) {
    const gx = x(max * i / 4);
    el('line', {x1: gx, x2: gx, y1: top, y2: H - 20, stroke: 'var(--grid)', 'stroke-width': 1}, svg);
    el('text', {x: gx, y: H - 6, 'text-anchor': 'middle', class: 'tick'}, svg).textContent = opts.tick(max * i / 4);
  }
  items.forEach((d, i) => {
    const y = top + i * rowH, bh = 16, by = y + (rowH - bh) / 2, w = Math.max(x(d.value) - left, 2);
    const label = el('text', {x: left - 8, y: by + bh / 2 + 4, 'text-anchor': 'end'}, svg);
    label.textContent = d.label.length > 24 ? d.label.slice(0, 23) + '…' : d.label;
    el('path', {d: `M${left},${by} h${w - 4} a4,4 0 0 1 4,4 v${bh - 8} a4,4 0 0 1 -4,4 h${-(w - 4)} z`,
                fill: d.color || 'var(--series-1)'}, svg);
    el('text', {x: left + w + 6, y: by + bh / 2 + 4, style: 'fill:var(--ink)'}, svg).textContent = opts.label(d.value);
    const hit = el('rect', {x: 0, y, width: W, height: rowH, fill: 'transparent'}, svg);
    hit.addEventListener('mousemove', e => { hit.setAttribute('fill', 'var(--hover)'); showTip(e, d.label, d.tip || [opts.label(d.value)]); });
    hit.addEventListener('mouseleave', () => { hit.setAttribute('fill', 'transparent'); hideTip(); });
  });
  el('line', {x1: left, x2: left, y1: top, y2: H - 20, stroke: 'var(--axis)', 'stroke-width': 1}, svg);
  return svg;
}

/* Line chart with area wash, crosshair and tooltip. */
function lineChart(points, opts) {
  const W = 1100, H = 300, L = 64, R = 16, T = 12, B = 34;
  const svg = el('svg', {viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': opts.title});
  const max = niceMax(Math.max(...points.map(p => p.value), 0));
  const x = i => L + (W - L - R) * (points.length === 1 ? 0.5 : i / (points.length - 1));
  const y = v => T + (H - T - B) * (1 - v / max);
  for (let i = 0; i <= 4; i++) {
    const gy = y(max * i / 4);
    el('line', {x1: L, x2: W - R, y1: gy, y2: gy, stroke: i ? 'var(--grid)' : 'var(--axis)', 'stroke-width': 1}, svg);
    el('text', {x: L - 8, y: gy + 4, 'text-anchor': 'end', class: 'tick'}, svg).textContent = opts.tick(max * i / 4);
  }
  const step = Math.ceil(points.length / 12);
  points.forEach((p, i) => { if (i % step === 0)
    el('text', {x: x(i), y: H - 12, 'text-anchor': 'middle', class: 'tick'}, svg).textContent = p.short; });
  const d = points.map((p, i) => (i ? 'L' : 'M') + x(i) + ',' + y(p.value)).join(' ');
  el('path', {d: d + ` L${x(points.length - 1)},${y(0)} L${x(0)},${y(0)} Z`, fill: 'var(--series-1)', opacity: 0.08}, svg);
  el('path', {d, fill: 'none', stroke: 'var(--series-1)', 'stroke-width': 2, 'stroke-linejoin': 'round'}, svg);
  const cross = el('line', {y1: T, y2: H - B, stroke: 'var(--axis)', 'stroke-width': 1, visibility: 'hidden'}, svg);
  const dot = el('circle', {r: 5, fill: 'var(--series-1)', stroke: 'var(--surface)', 'stroke-width': 2, visibility: 'hidden'}, svg);
  const hit = el('rect', {x: L, y: T, width: W - L - R, height: H - T - B, fill: 'transparent'}, svg);
  hit.addEventListener('mousemove', e => {
    const box = svg.getBoundingClientRect(); const sx = (e.clientX - box.left) * W / box.width;
    const i = Math.max(0, Math.min(points.length - 1, Math.round((sx - L) / ((W - L - R) / Math.max(points.length - 1, 1)))));
    const p = points[i];
    cross.setAttribute('x1', x(i)); cross.setAttribute('x2', x(i)); cross.setAttribute('visibility', 'visible');
    dot.setAttribute('cx', x(i)); dot.setAttribute('cy', y(p.value)); dot.setAttribute('visibility', 'visible');
    showTip(e, p.label, p.tip);
  });
  hit.addEventListener('mouseleave', () => { cross.setAttribute('visibility', 'hidden'); dot.setAttribute('visibility', 'hidden'); hideTip(); });
  return svg;
}

/* Table builder: cols = [{key, label, fmt, num, wrap, html}] */
function table(rows, cols) {
  if (!rows.length) return h('div', 'empty', 'No rows');
  const wrap = h('div', 'table-wrap'), t = h('table');
  t.innerHTML = '<thead><tr>' + cols.map(c => `<th class="${c.num ? 'num' : ''}">${esc(c.label)}</th>`).join('') + '</tr></thead>';
  const body = h('tbody');
  rows.forEach(r => { const tr = h('tr');
    tr.innerHTML = cols.map(c => { const v = r[c.key]; const s = c.html ? c.html(v, r) : esc(c.fmt ? c.fmt(v, r) : v);
      return `<td class="${c.num ? 'num' : ''}${c.wrap ? ' wrap' : ''}">${s}</td>`; }).join('');
    body.appendChild(tr); });
  t.appendChild(body); wrap.appendChild(t); return wrap;
}

/* Card with Chart | Table toggle (every chart has a table view). */
function card(title, desc, chartNode, tableNode, wide) {
  const c = h('div', 'card' + (wide ? ' wide' : ''));
  const head = h('div', 'card-head'); head.appendChild(h('h2', '', esc(title)));
  const body = h('div');
  if (chartNode && tableNode) {
    const tg = h('div', 'toggle'); const bc = h('button', '', 'Chart'), bt = h('button', '', 'Table');
    const set = showChart => { bc.setAttribute('aria-pressed', showChart); bt.setAttribute('aria-pressed', !showChart);
      body.replaceChildren(showChart ? chartNode : tableNode); };
    bc.onclick = () => set(true); bt.onclick = () => set(false); tg.append(bc, bt); head.appendChild(tg); set(true);
  } else { body.appendChild(chartNode || tableNode); }
  c.appendChild(head); if (desc) c.appendChild(h('p', 'desc', esc(desc))); c.appendChild(body); return c;
}
const ICON = {
  revenue: '<path d="M6 3h12M6 8h12M6 13l8.5 8M6 13h3a4.5 4.5 0 0 0 0-9"/>',
  cart: '<circle cx="8" cy="21" r="1"/><circle cx="19" cy="21" r="1"/><path d="M2.05 2.05h2l2.66 12.42a2 2 0 0 0 2 1.58h9.78a2 2 0 0 0 1.95-1.57l1.65-7.43H5.12"/>',
  receipt: '<path d="M4 2v20l2-1 2 1 2-1 2 1 2-1 2 1 2-1 2 1V2l-2 1-2-1-2 1-2-1-2 1-2-1-2 1Z"/><path d="M16 8h-6a2 2 0 1 0 0 4h4a2 2 0 1 1 0 4H8"/>',
  box: '<path d="m7.5 4.27 9 5.15"/><path d="M21 8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16Z"/><path d="m3.3 7 8.7 5 8.7-5M12 22V12"/>',
  users: '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75"/>',
  db: '<ellipse cx="12" cy="5" rx="9" ry="3"/><path d="M3 5v14a9 3 0 0 0 18 0V5"/><path d="M3 12a9 3 0 0 0 18 0"/>',
  chart: '<path d="M3 3v18h18"/><path d="M18 17V9M13 17V5M8 17v-3"/>',
  shield: '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/><path d="m9 12 2 2 4-4"/>',
  flow: '<rect width="8" height="8" x="3" y="3" rx="2"/><path d="M7 11v4a2 2 0 0 0 2 2h4"/><rect width="8" height="8" x="13" y="13" rx="2"/>',
  gauge: '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
  history: '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5M12 7v5l4 2"/>',
  alert: '<path d="M12 9v4M12 17h.01"/><circle cx="12" cy="12" r="10"/>',
  check: '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
};
const svgIcon = name => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICON[name] || ICON.chart}</svg>`;
const TONE = {brand: '#2a78d6', good: '#10b981', bad: '#f43f5e', neutral: '#334155', violet: '#8b5cf6', amber: '#f59e0b'};
const tile = (label, value, note, icon = 'chart', tone = 'brand') => { const t = h('div', 'tile');
  t.innerHTML = `<div class="row"><div class="label">${esc(label)}</div><span class="chip" style="background:${TONE[tone]}">${svgIcon(icon)}</span></div>`
    + `<div class="value">${esc(value)}</div>` + (note ? `<div class="note">${esc(note)}</div>` : ''); return t; };
const pageTitle = (root, eyebrow, title) => { root.appendChild(h('p', 'eyebrow', esc(eyebrow))); root.appendChild(h('h1', 'pagetitle', esc(title))); };
const badge = s => { const ok = s === 'PASS' || s === 'SUCCESS', bad = s === 'FAIL' || s === 'FAILED' || s === 'RECONCILIATION_FAILED';
  return `<span class="badge ${ok ? 'ok' : bad ? 'bad' : 'info'}"><i></i>${ok ? '✓ ' : bad ? '✕ ' : ''}${esc(s)}</span>`; };
const money = v => v == null ? '' : inr2.format(v);
const int = v => v == null ? '' : num.format(v);

/* ---------- Sales tab ---------- */
function salesTab(root) {
  const k = D.kpis;
  const cr = k.net_revenue >= 1e7 ? '₹' + (k.net_revenue / 1e7).toFixed(2) + ' Cr' : inr.format(k.net_revenue);
  const latest = D.runs && D.runs.length ? D.runs[0] : null;
  const hero = h('div', 'hero');
  hero.innerHTML = `<div style="position:relative;z-index:1;min-width:0"><p class="k">Net revenue · recognised orders</p><p class="big">${esc(cr)}</p>
      <p class="s">${esc(inr2.format(k.net_revenue))} from <b style="color:#fff">${esc(int(k.orders))}</b> orders. Cancelled and returned orders are excluded.</p></div>
    <div class="panel"><div style="display:flex;justify-content:space-between;align-items:center;gap:8px"><b>Latest pipeline run</b>${latest ? badge(latest.status) : ''}</div>
      ${latest ? `<dl><div style="grid-column:1/-1;min-width:0"><dt>Run ID</dt><dd style="font-family:Consolas,monospace;font-size:12px;overflow:hidden;text-overflow:ellipsis">${esc(latest.run_id)}</dd></div>
        <div><dt>Duration</dt><dd>${Number(latest.seconds || 0).toFixed(2)} s</dd></div><div><dt>Records in</dt><dd>${esc(int(latest.input_count))}</dd></div>
        <div><dt>Rejected</dt><dd>${esc(int(latest.rejected_count))}</dd></div><div><dt>New fact rows</dt><dd>${esc(int(latest.inserted_count))}</dd></div></dl>` : '<p class="s">No runs yet.</p>'}</div>`;
  pageTitle(root, 'Overview', 'Sales analytics');
  root.appendChild(hero);
  const tiles = h('div', 'tiles');
  tiles.append(tile('Net revenue', cr, 'excl. cancelled / returned', 'revenue', 'brand'),
    tile('Orders', int(k.orders), 'recognised orders', 'cart', 'violet'),
    tile('Average order value', k.aov == null ? '–' : inr.format(k.aov), 'per order, not per line', 'receipt', 'amber'),
    tile('Units sold', int(k.units), null, 'box', 'good'),
    tile('Customers', int(k.customers), `${int(k.products)} products`, 'users', 'neutral'), tile('Order lines', int(k.fact_rows), 'rows in fact_sales', 'db', 'brand'));
  root.appendChild(tiles);
  const grid = h('div', 'grid'); root.appendChild(grid);

  const months = D.monthly.map(m => ({label: `${m.month_name} ${m.year}`, short: `${m.month_name.slice(0, 3)} ${String(m.year).slice(2)}`,
    value: m.net_revenue, tip: [inr.format(m.net_revenue), `${int(m.orders)} orders`,
    m.mom_growth_pct == null ? 'first month' : `${m.mom_growth_pct > 0 ? '+' : ''}${m.mom_growth_pct}% vs previous month`]}));
  grid.appendChild(card('Monthly net revenue', 'Recognised revenue per calendar month (dim_date).',
    months.length ? lineChart(months, {title: 'Monthly net revenue', tick: compact}) : h('div', 'empty', 'No data'),
    table(D.monthly, [{key: 'year', label: 'Year'}, {key: 'month_name', label: 'Month'}, {key: 'orders', label: 'Orders', num: 1, fmt: int},
      {key: 'net_revenue', label: 'Net revenue', num: 1, fmt: money}, {key: 'mom_growth_pct', label: 'MoM %', num: 1}]), true));

  const bars = (rows, labelKey, valueKey, tipFn) => rows.map(r => ({label: String(r[labelKey]), value: r[valueKey], tip: tipFn(r)}));
  grid.appendChild(card('Revenue by year', 'Year-over-year growth in the tooltip and table.',
    barChart(bars(D.yearly, 'year', 'net_revenue', r => [inr.format(r.net_revenue), `${int(r.orders)} orders`,
      r.yoy_growth_pct == null ? 'first year' : `${r.yoy_growth_pct > 0 ? '+' : ''}${r.yoy_growth_pct}% YoY`]), {title: 'Revenue by year', tick: compact, label: compact, labelWidth: 60}),
    table(D.yearly, [{key: 'year', label: 'Year'}, {key: 'orders', label: 'Orders', num: 1, fmt: int}, {key: 'units_sold', label: 'Units', num: 1, fmt: int},
      {key: 'net_revenue', label: 'Net revenue', num: 1, fmt: money}, {key: 'yoy_growth_pct', label: 'YoY %', num: 1}])));
  grid.appendChild(card('Revenue by category', 'Category valid at the time of sale (SCD2).',
    barChart(bars(D.category, 'category', 'net_revenue', r => [inr.format(r.net_revenue), `${r.revenue_share_pct}% of revenue`, `${int(r.units_sold)} units`]),
      {title: 'Revenue by category', tick: compact, label: compact}),
    table(D.category, [{key: 'category', label: 'Category'}, {key: 'orders', label: 'Orders', num: 1, fmt: int}, {key: 'units_sold', label: 'Units', num: 1, fmt: int},
      {key: 'net_revenue', label: 'Net revenue', num: 1, fmt: money}, {key: 'revenue_share_pct', label: 'Share %', num: 1}])));
  const cities = D.city.slice(0, 10);
  grid.appendChild(card('Top 10 cities by revenue', 'City where the customer lived when they bought (SCD2).',
    barChart(bars(cities, 'city', 'net_revenue', r => [inr.format(r.net_revenue), r.state, `${int(r.customers)} customers · ${int(r.orders)} orders`]),
      {title: 'Top cities', tick: compact, label: compact, labelWidth: 120}),
    table(D.city, [{key: 'city', label: 'City'}, {key: 'state', label: 'State'}, {key: 'customers', label: 'Customers', num: 1, fmt: int},
      {key: 'orders', label: 'Orders', num: 1, fmt: int}, {key: 'net_revenue', label: 'Net revenue', num: 1, fmt: money}])));
  grid.appendChild(card('Revenue by payment method', null,
    barChart(bars(D.payment, 'payment_method', 'net_revenue', r => [inr.format(r.net_revenue), `${r.revenue_share_pct}% of revenue`, `AOV ${inr.format(r.average_order_value)}`]),
      {title: 'Payment methods', tick: compact, label: compact, labelWidth: 130}),
    table(D.payment, [{key: 'payment_method', label: 'Payment method'}, {key: 'orders', label: 'Orders', num: 1, fmt: int},
      {key: 'net_revenue', label: 'Net revenue', num: 1, fmt: money}, {key: 'average_order_value', label: 'AOV', num: 1, fmt: money},
      {key: 'revenue_share_pct', label: 'Share %', num: 1}])));
  grid.appendChild(card('Orders by status', 'All statuses, counted per order.',
    barChart(bars(D.status, 'order_status', 'orders', r => [`${int(r.orders)} orders (${r.pct_of_orders}%)`, `value ${inr.format(r.order_value)}`]),
      {title: 'Orders by status', tick: int, label: int, labelWidth: 100}),
    table(D.status, [{key: 'order_status', label: 'Status'}, {key: 'orders', label: 'Orders', num: 1, fmt: int}, {key: 'pct_of_orders', label: '% orders', num: 1},
      {key: 'units', label: 'Units', num: 1, fmt: int}, {key: 'order_value', label: 'Order value', num: 1, fmt: money}])));
  grid.appendChild(card('Customer segments (repeat buying)', null,
    barChart(bars(D.segments, 'segment', 'net_revenue', r => [inr.format(r.net_revenue), `${int(r.customers)} customers`, `avg spend ${inr.format(r.avg_spend_per_customer)}`]),
      {title: 'Customer segments', tick: compact, label: compact, labelWidth: 90}),
    table(D.segments, [{key: 'segment', label: 'Segment'}, {key: 'customers', label: 'Customers', num: 1, fmt: int}, {key: 'orders', label: 'Orders', num: 1, fmt: int},
      {key: 'net_revenue', label: 'Net revenue', num: 1, fmt: money}, {key: 'avg_spend_per_customer', label: 'Avg spend', num: 1, fmt: money}])));
  grid.appendChild(card('Top 10 products (units sold)', null, null,
    table(D.top_products, [{key: 'product_id', label: 'Product'}, {key: 'product_name', label: 'Name'}, {key: 'category', label: 'Category'},
      {key: 'units_sold', label: 'Units', num: 1, fmt: int}, {key: 'orders', label: 'Orders', num: 1, fmt: int}, {key: 'net_revenue', label: 'Net revenue', num: 1, fmt: money}])));
  grid.appendChild(card('Top 10 customers by spend', null, null,
    table(D.top_customers, [{key: 'customer_id', label: 'Customer'}, {key: 'customer_name', label: 'Name'}, {key: 'current_city', label: 'Current city'},
      {key: 'orders', label: 'Orders', num: 1, fmt: int}, {key: 'total_spent', label: 'Total spent', num: 1, fmt: money}])));
  if (D.scd_history.length) grid.appendChild(card('Customer history (SCD Type 2 versions)', 'Sales per customer version - old sales stay with the old city.', null,
    table(D.scd_history, [{key: 'customer_id', label: 'Customer'}, {key: 'customer_key', label: 'Key', num: 1}, {key: 'city', label: 'City'},
      {key: 'effective_start_date', label: 'Valid from'}, {key: 'effective_end_date', label: 'Valid to'},
      {key: 'is_current', label: 'Current', fmt: v => v ? 'yes' : 'no'}, {key: 'order_lines', label: 'Lines', num: 1, fmt: int},
      {key: 'net_revenue', label: 'Net revenue', num: 1, fmt: money}]), true));
}

/* ---------- Data quality tab ---------- */
function qualityTab(root) {
  pageTitle(root, 'Processing', 'Data quality');
  const k = D.kpis, recon = D.reconciliation;
  const crit = recon.filter(r => r.severity === 'CRITICAL'), passed = crit.filter(r => r.status === 'PASS').length;
  const tiles = h('div', 'tiles');
  tiles.append(tile('Rejected records (all runs)', int(k.rejected), null, 'alert', k.rejected ? 'bad' : 'neutral'), tile('Distinct reason codes', int(D.reasons.length), null, 'shield', 'amber'),
    tile('Reconciliation (latest run)', crit.length ? `${passed} / ${crit.length}` : '–', 'critical checks passed', 'check', 'good'));
  root.appendChild(tiles);
  const grid = h('div', 'grid'); root.appendChild(grid);
  grid.appendChild(card('Rejections by reason code', 'A record with several problems counts once per reason.',
    D.reasons.length ? barChart(D.reasons.map(r => ({label: r.reason_code, value: r.records, tip: [`${int(r.records)} records`]})),
      {title: 'Rejections by reason', tick: int, label: int, labelWidth: 200}) : h('div', 'empty', 'No rejected records'),
    table(D.reasons, [{key: 'reason_code', label: 'Reason code'}, {key: 'records', label: 'Records', num: 1, fmt: int}])));
  grid.appendChild(card('Rejected records by entity', null,
    D.entity_quality.length ? barChart(D.entity_quality.map(r => ({label: r.entity, value: r.rejected, tip: [`${int(r.rejected)} rejected`]})),
      {title: 'Rejected by entity', tick: int, label: int, labelWidth: 100}) : h('div', 'empty', 'No rejected records'),
    table(D.entity_quality, [{key: 'entity', label: 'Entity'}, {key: 'rejected', label: 'Rejected', num: 1, fmt: int}])));
  grid.appendChild(card('Reconciliation checks - latest run', recon.length ? `Run ${recon[0].run_id}` : 'No report yet', null,
    table(recon, [{key: 'status', label: 'Status', html: badge}, {key: 'severity', label: 'Severity'}, {key: 'check_name', label: 'Check'},
      {key: 'source_value', label: 'Source', num: 1}, {key: 'target_value', label: 'Target', num: 1}, {key: 'explanation', label: 'Explanation', wrap: 1}]), true));
  grid.appendChild(card('Quarantined records (latest 100)', 'ops.rejected_records - original values are kept as JSON in the database.', null,
    table(D.rejects, [{key: 'entity_name', label: 'Entity'}, {key: 'source_file', label: 'File'}, {key: 'row_no', label: 'Line', num: 1},
      {key: 'record_id', label: 'Record id'}, {key: 'reason_codes', label: 'Reason codes'}, {key: 'rejection_reason', label: 'Details', wrap: 1}]), true));
}

/* ---------- Pipeline runs tab ---------- */
function pipelineTab(root) {
  pageTitle(root, 'Processing', 'Pipeline runs');
  const runs = D.runs, ok = runs.filter(r => r.status === 'SUCCESS').length;
  const tiles = h('div', 'tiles');
  tiles.append(tile('Pipeline runs', int(D.kpis.runs), null, 'flow', 'brand'), tile('Successful', int(D.kpis.runs_ok), null, 'check', 'good'),
    tile('Latest run', runs.length ? runs[0].status : '–', runs.length ? runs[0].start_time : '', 'history', 'violet'));
  root.appendChild(tiles);
  const grid = h('div', 'grid'); root.appendChild(grid);
  const recent = runs.slice(0, 12).reverse();
  grid.appendChild(card('Run duration (latest runs)', 'Seconds per run; hover for counts.',
    recent.length ? barChart(recent.map(r => ({label: r.run_id.replace(/^run_/, '').slice(0, 22), value: Number(r.seconds || 0),
      color: r.status === 'SUCCESS' ? 'var(--series-1)' : 'var(--critical)',
      tip: [`${r.status}`, `${Number(r.seconds || 0).toFixed(2)} s`, `input ${int(r.input_count)} · rejected ${int(r.rejected_count)}`, `inserted ${int(r.inserted_count)} · updated ${int(r.updated_count)}`]})),
      {title: 'Run durations', tick: v => v.toFixed(1) + 's', label: v => v.toFixed(2) + 's', labelWidth: 170}) : h('div', 'empty', 'No runs'),
    table(runs, [{key: 'run_id', label: 'Run'}, {key: 'status', label: 'Status', html: badge}, {key: 'seconds', label: 'Seconds', num: 1}])));
  grid.appendChild(card('Pipeline run history (ops.pipeline_runs)', `${ok} of ${runs.length} shown runs succeeded.`, null,
    table(runs, [{key: 'run_id', label: 'Run id'}, {key: 'status', label: 'Status', html: badge}, {key: 'start_time', label: 'Started'},
      {key: 'seconds', label: 'Seconds', num: 1}, {key: 'input_count', label: 'Input', num: 1, fmt: int}, {key: 'valid_count', label: 'Valid', num: 1, fmt: int},
      {key: 'rejected_count', label: 'Rejected', num: 1, fmt: int}, {key: 'inserted_count', label: 'Inserted', num: 1, fmt: int},
      {key: 'updated_count', label: 'Updated', num: 1, fmt: int}, {key: 'error_message', label: 'Error', wrap: 1}]), true));
}

/* ---------- Performance tab ---------- */
function performanceTab(root) {
  pageTitle(root, 'Insights', 'Performance');
  const P = D.performance;
  if (!P) { root.appendChild(h('div', 'empty', 'No benchmark yet - run: python -m src.cli benchmark')); return; }
  const tiles = h('div', 'tiles'), largest = P.rows[P.rows.length - 1];
  tiles.append(tile('Largest run', int(largest.order_items) + ' lines', `${largest.total_s.toFixed(1)} s end to end`),
    tile('Throughput (largest)', int(Math.round(largest.rows_per_second)) + ' rows/s'),
    tile('Idempotent re-runs', P.rows.every(r => r.rerun_inserted === 0) ? '0 duplicates' : 'CHECK', 'same files loaded twice'));
  if (P.experiment) tiles.appendChild(tile('COPY vs row INSERT', (P.experiment.insert_s / P.experiment.copy_s).toFixed(0) + '× faster',
    `${int(P.experiment.rows)} rows: ${P.experiment.copy_s}s vs ${P.experiment.insert_s}s`));
  root.appendChild(tiles);
  const grid = h('div', 'grid'); root.appendChild(grid);
  grid.appendChild(card('Throughput by data size', 'Order lines per second, end to end.',
    barChart(P.rows.map(r => ({label: int(r.order_items) + ' lines', value: r.rows_per_second, tip: [`${int(Math.round(r.rows_per_second))} rows/s`, `total ${r.total_s}s`]})),
      {title: 'Throughput', tick: v => int(Math.round(v)), label: v => int(Math.round(v)) + '/s', labelWidth: 110}),
    table(P.rows, [{key: 'order_items', label: 'Order lines', num: 1, fmt: int}, {key: 'rows_per_second', label: 'Rows/s', num: 1, fmt: v => int(Math.round(v))}])));
  grid.appendChild(card('Where the time goes (largest size)', `${int(largest.order_items)} order lines - seconds per stage.`,
    barChart(['extract', 'validate', 'transform', 'load', 'reconcile'].map(s => ({label: s, value: largest[s + '_s'], tip: [`${largest[s + '_s']} s`,
      `${(100 * largest[s + '_s'] / largest.total_s).toFixed(0)}% of total`]})), {title: 'Stage timings', tick: v => v.toFixed(0) + 's', label: v => v.toFixed(1) + 's', labelWidth: 80}),
    table(['extract', 'validate', 'transform', 'load', 'reconcile'].map(s => ({stage: s, seconds: largest[s + '_s']})),
      [{key: 'stage', label: 'Stage'}, {key: 'seconds', label: 'Seconds', num: 1}])));
  grid.appendChild(card('Benchmark results', `Measured ${P.generated_at} · PostgreSQL ${P.postgres}`, null,
    table(P.rows, [{key: 'order_items', label: 'Order lines', num: 1, fmt: int}, {key: 'orders', label: 'Orders', num: 1, fmt: int},
      {key: 'generate_s', label: 'Generate s', num: 1}, {key: 'extract_s', label: 'Extract s', num: 1}, {key: 'validate_s', label: 'Validate s', num: 1},
      {key: 'transform_s', label: 'Transform s', num: 1}, {key: 'load_s', label: 'Load s', num: 1}, {key: 'reconcile_s', label: 'Reconcile s', num: 1},
      {key: 'total_s', label: 'Total s', num: 1}, {key: 'rows_per_second', label: 'Rows/s', num: 1, fmt: v => int(Math.round(v))},
      {key: 'rerun_s', label: 'Re-run s', num: 1}, {key: 'rerun_inserted', label: 'Re-run inserted', num: 1}, {key: 'status', label: 'Status'}]), true));
}


/* ---------- History & late data tab (scenario demo database) ---------- */
function historyTab(root) {
  pageTitle(root, 'Insights', 'History & late data');
  const H = D.history;
  if (!H) { root.appendChild(h('div', 'empty', 'No scenario data yet - run: python -m src.cli demo-scenarios')); return; }
  root.appendChild(h('p', 'desc', 'Four small batches loaded in order into the isolated demo database ' + esc(H.database) +
    '. Customer C1 moves from Hyderabad to Chennai on 1 Jun 2024, an April order arrives after the move, product P1 is ' +
    're-categorised, and one line references an unknown product.'));
  const grid = h('div', 'grid'); root.appendChild(grid);
  const cur = v => v ? '<span class="badge ok"><i></i>current</span>' : '<span class="badge info"><i></i>expired</span>';
  grid.appendChild(card('Batches loaded', null, null, table(H.runs, [{key: 'run_id', label: 'Run'}, {key: 'status', label: 'Status', html: badge},
    {key: 'input_count', label: 'Input', num: 1}, {key: 'rejected_count', label: 'Rejected', num: 1},
    {key: 'inserted_count', label: 'Inserted', num: 1}, {key: 'updated_count', label: 'Updated', num: 1}])));
  grid.appendChild(card('Quarantined line', 'Unknown product: no fact row and no broken foreign key.', null,
    table(H.rejects, [{key: 'source_record_id', label: 'Record'}, {key: 'reason_codes', label: 'Reason'},
      {key: 'rejection_reason', label: 'Details', wrap: 1}])));
  grid.appendChild(card('Customer versions (SCD Type 2)', 'The old version closes at the change instant and the new one starts there.', null,
    table(H.customers, [{key: 'customer_key', label: 'Key', num: 1}, {key: 'customer_id', label: 'Customer'}, {key: 'city', label: 'City'},
      {key: 'state', label: 'State'}, {key: 'effective_start_date', label: 'Valid from'}, {key: 'effective_end_date', label: 'Valid to'},
      {key: 'is_current', label: 'Version', html: cur}]), true));
  grid.appendChild(card('Product versions (SCD Type 2)', null, null,
    table(H.products, [{key: 'product_key', label: 'Key', num: 1}, {key: 'product_id', label: 'Product'}, {key: 'category', label: 'Category'},
      {key: 'brand', label: 'Brand'}, {key: 'effective_start_date', label: 'Valid from'}, {key: 'effective_end_date', label: 'Valid to'},
      {key: 'is_current', label: 'Version', html: cur}]), true));
  grid.appendChild(card('Each sale is linked to the version valid on its order date',
    'OI4 (20 Apr, loaded last) still gets the Hyderabad customer and Electronics product versions. OI3 (15 Jul) gets Chennai and Mobiles.', null,
    table(H.facts, [{key: 'order_item_id', label: 'Line'}, {key: 'order_id', label: 'Order'}, {key: 'date_key', label: 'Date key', num: 1},
      {key: 'customer_key', label: 'Cust. key', num: 1}, {key: 'city', label: 'City at sale'}, {key: 'product_key', label: 'Prod. key', num: 1},
      {key: 'category', label: 'Category at sale'}, {key: 'quantity', label: 'Qty', num: 1},
      {key: 'net_revenue', label: 'Net revenue', num: 1, fmt: money}]), true));
}

/* ---------- Shell ---------- */
document.getElementById('meta').textContent = `${D.database} · ${D.generated_at} · ${int(D.kpis.fact_rows)} fact rows`;
document.getElementById('pilltext').textContent = `Snapshot ${D.generated_at}`;
const TABS = [['sales', 'Sales analytics', salesTab, 'gauge', 'Revenue, products, customers and trends'],
              ['quality', 'Data quality', qualityTab, 'shield', 'Rejected records and reconciliation'],
              ['pipeline', 'Pipeline runs', pipelineTab, 'flow', 'Every run with its timings and counts'],
              ['performance', 'Performance', performanceTab, 'chart', 'Benchmark results and bottlenecks'],
              ['history', 'History & late data', historyTab, 'history', 'SCD Type 2 and late-arriving data']];
const nav = document.getElementById('tabs');
const select = (id) => {
  const t = TABS.find(x => x[0] === id) || TABS[0];
  nav.querySelectorAll('button').forEach(x => x.setAttribute('aria-selected', x.dataset.id === t[0]));
  TABS.forEach(([other]) => document.getElementById('tab-' + other).hidden = other !== t[0]);
  document.getElementById('toptitle').textContent = t[1];
  document.getElementById('topdesc').textContent = t[4];
  document.getElementById('topicon').innerHTML = svgIcon(t[3]);
  document.body.classList.remove('nav-open'); hideTip();
};
TABS.forEach(([id, label, render, icon]) => {
  const b = h('button', '', svgIcon(icon) + esc(label)); b.dataset.id = id; b.setAttribute('role', 'tab');
  b.onclick = () => { select(id); if (location.hash !== '#' + id) history.replaceState(null, '', '#' + id); };
  nav.appendChild(b); render(document.getElementById('tab-' + id));
});
const openFromHash = () => select((location.hash || '#sales').slice(1));
window.addEventListener('hashchange', openFromHash); openFromHash();
document.getElementById('menu').onclick = () => document.body.classList.toggle('nav-open');
</script>
</body>
</html>
"""
