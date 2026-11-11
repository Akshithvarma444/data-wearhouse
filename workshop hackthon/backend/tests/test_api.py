"""HTTP API tests: every endpoint used by the frontend, against the isolated test database."""

from __future__ import annotations

import io
import json
import shutil
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app.api.deps import engine_dep, settings_dep
from app.config.settings import Settings
from app.main import app
from app.services.run_tracker import tracker
from tests.conftest import SAMPLE_DIR, scalar

INVALID_DIR = SAMPLE_DIR.parent / "invalid"
EXPECTED = json.loads((Path(__file__).parent / "fixtures" / "expected_sample_results.json")
                      .read_text(encoding="utf-8"))


@pytest.fixture
def api_settings(settings: Settings, tmp_path: Path) -> Settings:
    datasets = tmp_path / "datasets"
    for name in ("generated", "uploads"):
        (datasets / name).mkdir(parents=True)
    shutil.copytree(SAMPLE_DIR, datasets / "sample")
    shutil.copytree(INVALID_DIR, datasets / "invalid")
    return settings.model_copy(update={"datasets_dir": datasets, "reports_dir": tmp_path / "reports"})


@pytest.fixture
def client(db: Engine, api_settings: Settings):
    app.dependency_overrides[engine_dep] = lambda: db
    app.dependency_overrides[settings_dep] = lambda: api_settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def run_and_wait(client: TestClient, source: str, timeout: float = 120.0) -> dict:
    response = client.post("/api/pipeline/run", json={"source": source, "strict_schema": False})
    assert response.status_code == 202, response.text
    run_id = response.json()["run_id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = client.get(f"/api/pipeline/status/{run_id}").json()
        if status["status"] != "RUNNING" and tracker.active_run is None:
            return status
        time.sleep(0.2)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def test_health_and_config(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["database"]["connected"] is True
    assert health["database"]["name"].endswith("_test")
    config = client.get("/api/settings/config").json()
    assert config["batch_size"] > 0 and config["cors_origins"]
    assert "password" not in json.dumps(config).lower()


def test_pipeline_run_status_and_idempotent_rerun(client: TestClient, db: Engine) -> None:
    first = run_and_wait(client, "sample")
    assert first["status"] == "SUCCESS"
    assert first["progress_percent"] == 100
    assert all(s["status"] == "done" for s in first["stages"])
    assert first["reconciliation_passed"] is True
    assert first["inserted_count"] == 100 and first["input_count"] == 208
    second = run_and_wait(client, "sample")
    assert second["inserted_count"] == 0 and second["status"] == "SUCCESS"
    assert scalar(db, "SELECT count(*) FROM dw.fact_sales") == 100
    history = client.get("/api/pipeline/history").json()
    assert [r["run_id"] for r in history[:2]] == [second["run_id"], first["run_id"]]
    assert client.get(f"/api/history/{first['run_id']}").json()["status"] == "SUCCESS"


def test_status_of_unknown_run_is_404(client: TestClient) -> None:
    assert client.get("/api/pipeline/status/run_does_not_exist").status_code == 404


def test_run_rejects_incomplete_dataset(client: TestClient) -> None:
    response = client.post("/api/pipeline/run", json={"source": "uploads"})
    assert response.status_code == 422
    assert "missing" in response.json()["detail"]


def test_run_rejects_unknown_source(client: TestClient) -> None:
    assert client.post("/api/pipeline/run", json={"source": "../etc"}).status_code == 422


def test_dashboard_analytics_and_reports_use_real_data(client: TestClient) -> None:
    empty = client.get("/api/dashboard/summary").json()
    assert empty["total_orders"] == 0 and empty["latest_run"] is None
    run_and_wait(client, "sample")

    summary = client.get("/api/dashboard/summary").json()
    assert summary["total_orders"] == EXPECTED["total_revenue"]["orders"]
    assert f"{summary['total_revenue']:.2f}" == EXPECTED["total_revenue"]["net_revenue"]
    assert summary["pipeline_success_rate"] == 100.0
    assert summary["processed_records"] == 208

    revenue = client.get("/api/analytics/revenue").json()
    assert f"{revenue['average_order_value']:.2f}" == EXPECTED["average_order_value"]
    categories = client.get("/api/analytics/category-sales").json()
    assert {c["category"]: f"{c['net_revenue']:.2f}" for c in categories} == EXPECTED["category_revenue"]
    products = client.get("/api/analytics/top-products").json()
    assert [p["product_id"] for p in products] == EXPECTED["top_10_products"]
    assert len(client.get("/api/analytics/top-customers?limit=5").json()) == 5
    monthly = client.get("/api/analytics/monthly-sales").json()
    assert monthly and {"period", "net_revenue", "mom_growth_pct"} <= set(monthly[0])

    recon = client.get("/api/reports/reconciliation").json()
    assert recon["passed"] is True and len(recon["checks"]) == 21
    perf = client.get("/api/reports/performance").json()
    assert perf["latest_runs"][0]["order_items"] == 100

    csv_export = client.get("/api/reports/export?report=reconciliation&format=csv")
    assert csv_export.status_code == 200
    assert "attachment" in csv_export.headers["content-disposition"]
    assert csv_export.text.splitlines()[0].startswith("run_id,check_name")
    history_json = client.get("/api/reports/export?report=history&format=json").json()
    assert history_json[0]["status"] == "SUCCESS"
    assert client.get("/api/reports/export?report=nope").status_code == 422


def test_quality_endpoints_after_invalid_run(client: TestClient) -> None:
    status = run_and_wait(client, "invalid")
    assert status["status"] == "SUCCESS" and status["rejected_count"] == 25
    quality = client.get("/api/quality/summary").json()
    assert quality["rejected_records"] == 25
    assert quality["duplicate_records"] == 6
    assert quality["invalid_references"] == 4
    assert quality["missing_values"] == 2
    page = client.get("/api/quality/rejections?reason=EXCESSIVE_DISCOUNT&limit=10").json()
    assert page["total"] == 2
    assert all("EXCESSIVE_DISCOUNT" in r["reason_codes"] for r in page["items"])
    assert page["items"][0]["raw_record"]
    by_entity = client.get("/api/quality/rejections?entity=products").json()
    assert by_entity["total"] == 3
    assert client.get("/api/quality/rejections?reason=bad;drop").status_code == 422
    export = client.get("/api/reports/export?report=rejections&format=csv")
    assert export.text.count("\n") == 26  # header + 25 rows


def test_warehouse_catalog(client: TestClient) -> None:
    run_and_wait(client, "sample")
    tables = {t["name"]: t for t in client.get("/api/warehouse/tables").json()}
    assert tables["fact_sales"]["kind"] == "fact" and tables["fact_sales"]["row_count"] == 100
    assert tables["dim_customer"]["schema"] == "dw"
    fact = client.get("/api/warehouse/tables/fact_sales?limit=3").json()
    columns = {c["name"]: c for c in fact["columns"]}
    assert columns["sales_key"]["is_primary_key"] is True
    assert columns["customer_key"]["foreign_key"] == "dw.dim_customer(customer_key)"
    assert columns["unit_price"]["data_type"] == "numeric(12,2)"
    assert len(fact["sample_rows"]) == 3
    assert client.get("/api/warehouse/tables/pg_shadow").status_code == 404


def test_generator_and_download(client: TestClient, api_settings: Settings) -> None:
    response = client.post("/api/generator/generate", json={
        "customers": 50, "products": 20, "orders": 100, "seed": 7, "include_invalid": True})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["counts"]["customers"] == 50 and body["counts"]["orders"] == 100
    assert body["counts"]["order_items"] >= 100
    assert body["injected_defects"] == 23
    lines = (api_settings.datasets_dir / "generated" / "order_items.csv").read_text().count("\n")
    assert lines == body["counts"]["order_items"] + 1
    archive = client.get("/api/generator/download?dataset=generated")
    assert archive.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(archive.content)).namelist()
    assert sorted(names) == ["customers.csv", "order_items.csv", "orders.csv", "products.csv"]
    bad = client.post("/api/generator/generate", json={"customers": 0, "products": 1, "orders": 1, "seed": 1})
    assert bad.status_code == 422


def test_upload_and_dataset_listing(client: TestClient) -> None:
    content = (SAMPLE_DIR / "customers.csv").read_bytes()
    ok = client.post("/api/datasets/upload", data={"entity": "customers"},
                     files={"file": ("my customers.csv", content, "text/csv")})
    assert ok.status_code == 201, ok.text
    assert ok.json()["status"] == "ready" and ok.json()["record_count"] == 40
    wrong_ext = client.post("/api/datasets/upload", data={"entity": "customers"},
                            files={"file": ("customers.txt", content, "text/plain")})
    assert wrong_ext.status_code == 422
    missing_col = client.post("/api/datasets/upload", data={"entity": "orders"},
                              files={"file": ("orders.csv", b"order_id,order_date\nO1,2024-01-01 00:00:00\n", "text/csv")})
    assert missing_col.status_code == 422 and "customer_id" in missing_col.json()["detail"]
    empty = client.post("/api/datasets/upload", data={"entity": "products"},
                        files={"file": ("products.csv", b"", "text/csv")})
    assert empty.status_code == 422
    bad_entity = client.post("/api/datasets/upload", data={"entity": "../../etc"},
                             files={"file": ("x.csv", content, "text/csv")})
    assert bad_entity.status_code == 422
    listing = {s["source"]: s for s in client.get("/api/datasets").json()["sources"]}
    uploads = {f["entity"]: f for f in listing["uploads"]["files"]}
    assert uploads["customers"]["status"] == "ready" and uploads["orders"]["status"] == "missing"
    sample = {f["entity"]: f for f in listing["sample"]["files"]}
    assert sample["order_items"]["record_count"] == 100


def test_uploaded_files_run_through_pipeline(client: TestClient) -> None:
    for entity in ("customers", "products", "orders", "order_items"):
        r = client.post("/api/datasets/upload", data={"entity": entity},
                        files={"file": (f"{entity}.csv", (SAMPLE_DIR / f"{entity}.csv").read_bytes(), "text/csv")})
        assert r.status_code == 201
    status = run_and_wait(client, "uploads")
    assert status["status"] == "SUCCESS" and status["inserted_count"] == 100
