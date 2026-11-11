"""Warehouse catalog endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import Engine

from app.api.deps import engine_dep
from app.schemas.api import WarehouseTable, WarehouseTableDetail
from app.services import reporting

router = APIRouter(prefix="/api/warehouse", tags=["warehouse"])


@router.get("/tables", response_model=list[WarehouseTable], response_model_by_alias=True)
def tables(engine: Engine = Depends(engine_dep)) -> list:
    """Fact, dimension, source and operational tables with row counts."""
    return reporting.warehouse_tables(engine)


@router.get("/tables/{table_name}", response_model=WarehouseTableDetail, response_model_by_alias=True)
def table(table_name: str, limit: int = Query(10, ge=0, le=100),
          engine: Engine = Depends(engine_dep)) -> dict:
    """Columns, data types, primary/foreign keys and sample rows of one table."""
    detail = reporting.warehouse_table(engine, table_name, limit)
    if detail is None:
        raise HTTPException(404, f"unknown table '{table_name}'")
    return detail
