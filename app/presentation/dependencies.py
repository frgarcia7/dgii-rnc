"""Proveedores de dependencias FastAPI, control de origen sync y rate limiting."""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from app.application.use_cases import (
    BatchLookupRncUseCase,
    CheckHealthUseCase,
    GetContribuyenteByRncUseCase,
    GetStatsUseCase,
    InitializeCatalogUseCase,
    SearchContribuyentesUseCase,
    SyncRncCatalogUseCase,
    ValidateRncUseCase,
)
from app.core.config import Settings
from app.presentation.rate_limit import enforce_rate_limit, is_loopback_request


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_rnc_use_case(request: Request) -> GetContribuyenteByRncUseCase:
    return request.app.state.container.get_by_rnc_uc


def get_validate_use_case(request: Request) -> ValidateRncUseCase:
    return request.app.state.container.validate_rnc_uc


def get_batch_use_case(request: Request) -> BatchLookupRncUseCase:
    return request.app.state.container.batch_lookup_uc


def get_search_use_case(request: Request) -> SearchContribuyentesUseCase:
    return request.app.state.container.search_uc


def get_stats_use_case(request: Request) -> GetStatsUseCase:
    return request.app.state.container.stats_uc


def get_sync_use_case(request: Request) -> SyncRncCatalogUseCase:
    return request.app.state.container.sync_uc


def get_init_use_case(request: Request) -> InitializeCatalogUseCase:
    return request.app.state.container.init_uc


def get_health_use_case(request: Request) -> CheckHealthUseCase:
    return request.app.state.container.health_uc


def require_local_sync(request: Request) -> None:
    """Restringe POST /sync a localhost salvo que ALLOW_REMOTE_SYNC=true."""
    settings: Settings = request.app.state.settings
    if not settings.api.allow_remote_sync and not is_loopback_request(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "La sincronización manual solo está permitida desde localhost por defecto. "
                "Usa 'python -m app.cli sync' o habilita ALLOW_REMOTE_SYNC=true "
                "detrás de tu proxy/política de acceso."
            ),
        )


def rate_limit_lookup(request: Request) -> None:
    enforce_rate_limit(request, "lookup")


def rate_limit_search(request: Request) -> None:
    enforce_rate_limit(request, "search")


def rate_limit_batch(request: Request) -> None:
    enforce_rate_limit(request, "batch")


def rate_limit_sync(request: Request) -> None:
    enforce_rate_limit(request, "sync")
