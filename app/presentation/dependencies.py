"""Proveedores de dependencias FastAPI y autenticación opcional por API Key."""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request, status

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


def _extract_provided_key(x_api_key: str | None, authorization: str | None) -> str | None:
    if x_api_key:
        return x_api_key.strip()
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
        return token or None
    return None


def _verify_expected_key(
    expected_key: str | None,
    x_api_key: str | None,
    authorization: str | None,
) -> None:
    if not expected_key:
        return
    provided = _extract_provided_key(x_api_key, authorization)
    if not provided or not hmac.compare_digest(provided, expected_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key inválida o faltante (use header X-API-Key o Authorization: Bearer).",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """Valida API_KEY opcional en endpoints de consulta."""
    settings: Settings = request.app.state.settings
    _verify_expected_key(settings.api.api_key, x_api_key, authorization)


def require_sync_api_key(
    request: Request,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> None:
    """Valida SYNC_API_KEY (o API_KEY de respaldo) en endpoints administrativos."""
    settings: Settings = request.app.state.settings
    expected = settings.api.sync_api_key or settings.api.api_key
    _verify_expected_key(expected, x_api_key, authorization)
