"""Analytics endpoints (named queries in other/sql/analytical_queries.sql)."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import Engine

from app.api.deps import engine_dep
from app.schemas.api import CategorySales, MonthlySales, RevenueOverview, TopCustomer, TopProduct
from app.services import reporting

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/revenue", response_model=RevenueOverview)
def revenue(engine: Engine = Depends(engine_dep)) -> dict:
    """Totals, AOV, yearly sales, order status, city and payment-method revenue."""
    return reporting.revenue_overview(engine)


@router.get("/monthly-sales", response_model=list[MonthlySales])
def monthly_sales(engine: Engine = Depends(engine_dep)) -> list:
    return reporting.monthly_sales(engine)


@router.get("/top-products", response_model=list[TopProduct])
def top_products(limit: int = Query(10, ge=1, le=10), engine: Engine = Depends(engine_dep)) -> list:
    return reporting.top_products(engine, limit)


@router.get("/top-customers", response_model=list[TopCustomer])
def top_customers(limit: int = Query(10, ge=1, le=10), engine: Engine = Depends(engine_dep)) -> list:
    return reporting.top_customers(engine, limit)


@router.get("/category-sales", response_model=list[CategorySales])
def category_sales(engine: Engine = Depends(engine_dep)) -> list:
    return reporting.category_sales(engine)
