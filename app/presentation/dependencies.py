"""Proveedores de dependencias FastAPI, autenticación y rate limiting."""

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


def has_valid_sync_key(
    settings: Settings,
    x_api_key: str | None,
    authorization: str | None,
) -> bool:
    """Indica si el llamador presentó una clave administrativa válida."""
    expected = settings.api.sync_api_key or settings.api.api_key
    if not expected:
        return False
    provided = _extract_provided_key(x_api_key, authorization)
    return bool(provided and hmac.compare_digest(provided, expected))


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
    """Valida SYNC_API_KEY (o loopback en modo seguro) en endpoints administrativos."""
    settings: Settings = request.app.state.settings
    expected = settings.api.sync_api_key or settings.api.api_key
    if expected:
        _verify_expected_key(expected, x_api_key, authorization)
        return

    if not settings.api.allow_unauthenticated_sync and not is_loopback_request(request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "La sincronización manual sin SYNC_API_KEY solo está permitida desde localhost. "
                "Configura SYNC_API_KEY o utiliza 'python -m app.cli sync'."
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
