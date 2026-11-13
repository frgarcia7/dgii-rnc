"""Endpoint REST para disparar la sincronización manual con la DGII."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, status

from app.application.use_cases import SyncRncCatalogUseCase
from app.presentation.dependencies import get_sync_use_case
from app.presentation.schemas.contribuyente import SyncResponse

router = APIRouter(prefix="/api", tags=["Sincronización"])


@router.post(
    "/sync",
    response_model=SyncResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ejecutar sincronización manual",
    description=(
        "Descarga el archivo ZIP oficial de la DGII en segundo plano utilizando "
        "`BackgroundTasks` y actualiza el padrón en PostgreSQL mediante `UPSERT` por lotes. "
        "Si ya hay una sincronización en curso, evita lanzar una segunda ejecución paralela."
    ),
)
def trigger_sync(
    background_tasks: BackgroundTasks,
    use_case: SyncRncCatalogUseCase = Depends(get_sync_use_case),
) -> SyncResponse:
    if use_case.is_running():
        return SyncResponse(
            status="running",
            message="Ya existe una sincronización en curso.",
        )

    background_tasks.add_task(use_case.execute)
    return SyncResponse(
        status="accepted",
        message="Sincronización iniciada en segundo plano.",
    )

