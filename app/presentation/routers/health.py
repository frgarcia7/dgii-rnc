"""Endpoint de verificación de salud del servicio."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.application.use_cases import CheckHealthUseCase
from app.presentation.dependencies import get_health_use_case
from app.presentation.schemas.contribuyente import ErrorResponse, HealthResponse

router = APIRouter(tags=["Sistema"])


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Verificar estado del servicio",
    description="Comprueba que la API esté activa y que PostgreSQL responda correctamente.",
    responses={
        status.HTTP_503_SERVICE_UNAVAILABLE: {
            "model": ErrorResponse,
            "description": "Base de datos no disponible.",
        },
    },
)
def healthcheck(
    use_case: CheckHealthUseCase = Depends(get_health_use_case),
) -> HealthResponse:
    if not use_case.execute():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Base de datos no disponible.",
        )
    return HealthResponse(status="ok", database="connected")

