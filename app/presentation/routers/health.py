"""Endpoint de verificación de salud del servicio."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.use_cases import CheckHealthUseCase, InitializeCatalogUseCase
from app.core.config import Settings
from app.presentation.dependencies import (
    get_health_use_case,
    get_init_use_case,
    get_settings,
)
from app.presentation.schemas.contribuyente import ErrorResponse, HealthResponse

router = APIRouter(tags=["Sistema"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Verificar estado del servicio",
    description=(
        "Comprueba que la API esté activa, que PostgreSQL responda correctamente "
        "y si el padrón ya tiene registros disponibles."
    ),
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "Base de datos no disponible.",
        },
    },
)
def healthcheck(
    health_uc: CheckHealthUseCase = Depends(get_health_use_case),
    init_uc: InitializeCatalogUseCase = Depends(get_init_use_case),
    settings: Settings = Depends(get_settings),
) -> HealthResponse:
    healthy, catalog_ready, total = health_uc.get_health_snapshot()
    if not healthy:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Base de datos no disponible.",
        )

    initializing = init_uc.is_initializing()
    return HealthResponse(
        status="ok",
        database="connected",
        catalog_ready=catalog_ready,
        initializing=initializing,
        total_contribuyentes=total,
        batch_max_items=settings.api.batch_max_items,
    )
