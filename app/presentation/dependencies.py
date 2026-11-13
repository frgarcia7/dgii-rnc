"""Proveedores de dependencias FastAPI conectados al contenedor en app.state."""

from __future__ import annotations

from fastapi import Request

from app.application.use_cases import (
    CheckHealthUseCase,
    GetContribuyenteByRncUseCase,
    SearchContribuyentesUseCase,
    SyncRncCatalogUseCase,
)


def get_rnc_use_case(request: Request) -> GetContribuyenteByRncUseCase:
    return request.app.state.container.get_by_rnc_uc


def get_search_use_case(request: Request) -> SearchContribuyentesUseCase:
    return request.app.state.container.search_uc


def get_sync_use_case(request: Request) -> SyncRncCatalogUseCase:
    return request.app.state.container.sync_uc


def get_health_use_case(request: Request) -> CheckHealthUseCase:
    return request.app.state.container.health_uc

