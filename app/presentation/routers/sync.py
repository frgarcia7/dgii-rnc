"""Endpoints REST para disparar y monitorear sincronizaciones con la DGII."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.application.use_cases import SyncRncCatalogUseCase
from app.core.config import Settings
from app.domain.exceptions import SyncAlreadyRunningError
from app.presentation.dependencies import (
    get_settings,
    get_sync_use_case,
    require_api_key,
    require_sync_api_key,
)
from app.presentation.schemas.contribuyente import (
    ErrorResponse,
    SyncResponse,
    SyncRunResponse,
)

router = APIRouter(tags=["Sincronización"])


@router.post(
    "/sync",
    response_model=SyncResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Ejecutar sincronización manual",
    description=(
        "Descarga el archivo oficial de la DGII en segundo plano utilizando "
        "`BackgroundTasks` y actualiza el padrón en PostgreSQL mediante `UPSERT` por lotes. "
        "Si `SYNC_API_KEY` o `API_KEY` están configurados, requiere autenticación."
    ),
    dependencies=[Depends(require_sync_api_key)],
    responses={
        status.HTTP_401_UNAUTHORIZED: {
            "model": ErrorResponse,
            "description": "API key inválida o faltante.",
        },
        status.HTTP_403_FORBIDDEN: {
            "model": ErrorResponse,
            "description": "Sincronización manual HTTP deshabilitada.",
        },
        status.HTTP_409_CONFLICT: {
            "model": ErrorResponse,
            "description": "Ya existe una sincronización en progreso.",
        },
    },
)
def trigger_sync(
    background_tasks: BackgroundTasks,
    force: bool = Query(
        False,
        description="Si es true, ignora ETag/SHA-256 y fuerza reprocesar todo el padrón.",
    ),
    use_case: SyncRncCatalogUseCase = Depends(get_sync_use_case),
    settings: Settings = Depends(get_settings),
) -> SyncResponse:
    if not settings.api.enable_manual_sync:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La sincronización manual vía HTTP está deshabilitada en esta instancia.",
        )

    if use_case.is_running():
        raise SyncAlreadyRunningError("Ya existe una sincronización en curso.")

    background_tasks.add_task(use_case.execute, force=force)
    return SyncResponse(
        status="accepted",
        message="Sincronización iniciada en segundo plano.",
    )


@router.get(
    "/sync/status",
    response_model=SyncResponse,
    summary="Consultar estado de la sincronización",
    description=(
        "Indica si hay una sincronización en curso y muestra el último resultado registrado."
    ),
    dependencies=[Depends(require_api_key)],
)
def get_sync_status(
    use_case: SyncRncCatalogUseCase = Depends(get_sync_use_case),
) -> SyncResponse:
    latest = use_case.get_latest_run()
    running = use_case.is_running()
    current_status = "running" if running else (latest.status if latest else "never_run")
    message = (
        "Sincronización actualmente en progreso."
        if running
        else (
            f"Última sincronización: {latest.status}."
            if latest
            else "Aún no se ha registrado ninguna sincronización."
        )
    )
    return SyncResponse(
        status=current_status,
        message=message,
        run=SyncRunResponse.from_domain(latest) if latest else None,
    )
