"""Synthetic data generation endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.concurrency import run_in_threadpool

from app.api.deps import settings_dep
from app.config.settings import Settings
from app.schemas.api import GenerateRequest, GenerateResponse
from app.services import generator_service
from app.services.run_tracker import tracker

router = APIRouter(prefix="/api/generator", tags=["generator"])


@router.post("/generate", response_model=GenerateResponse)
async def generate(body: GenerateRequest, settings: Settings = Depends(settings_dep)) -> dict:
    """Generate the clean dataset (and optionally the invalid one) with a fixed seed."""
    if tracker.active_run is not None:
        raise HTTPException(409, "A pipeline run is reading the datasets; generate again when it finishes.")
    return await run_in_threadpool(generator_service.generate, settings, body.customers,
                                   body.products, body.orders, body.seed, body.include_invalid)


@router.get("/download")
def download(dataset: Literal["generated", "invalid"] = Query("generated"),
             settings: Settings = Depends(settings_dep)) -> Response:
    """Download a dataset folder as a .zip of CSV files."""
    try:
        content, name = generator_service.zip_dataset(settings, dataset)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return Response(content, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
