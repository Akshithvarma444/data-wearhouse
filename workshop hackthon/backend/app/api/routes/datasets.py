"""Dataset listing and CSV upload endpoints."""

from typing import Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

from app.api.deps import settings_dep
from app.config.settings import Settings
from app.schemas.api import DatasetFile, DatasetsResponse
from app.services import datasets as dataset_service
from app.services.run_tracker import tracker

router = APIRouter(prefix="/api/datasets", tags=["datasets"])


@router.get("", response_model=DatasetsResponse)
def list_datasets(settings: Settings = Depends(settings_dep)) -> dict:
    """The four entity files of every dataset source with record counts and schema check."""
    return dataset_service.list_datasets(settings)


@router.post("/upload", response_model=DatasetFile, status_code=201)
async def upload(entity: Literal["customers", "products", "orders", "order_items"] = Form(...),
                 file: UploadFile = File(...),
                 settings: Settings = Depends(settings_dep)) -> dict:
    """Upload one entity CSV into the 'uploads' dataset (stored as <entity>.csv)."""
    if tracker.active_run is not None:
        raise HTTPException(409, "A pipeline run is in progress; upload again when it finishes.")
    try:
        return await run_in_threadpool(dataset_service.save_upload, settings, entity,
                                       file.filename, file.file)
    except dataset_service.UploadError as exc:
        raise HTTPException(422, str(exc)) from exc
    finally:
        await file.close()
