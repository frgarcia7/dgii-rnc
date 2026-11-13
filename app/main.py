"""Composition Root de la aplicación FastAPI (Arquitectura Limpia + Swagger/OpenAPI)."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app.application.use_cases import (
    CheckHealthUseCase,
    GetContribuyenteByRncUseCase,
    InitializeCatalogUseCase,
    SearchContribuyentesUseCase,
    SyncRncCatalogUseCase,
)
from app.core.config import Settings
from app.infrastructure.db.connection import PostgresConnectionManager
from app.infrastructure.db.postgres_repository import PostgresContribuyenteRepository
from app.infrastructure.dgii.http_zip_source import HttpDgiiZipSource
from app.infrastructure.scheduler.cron import DailySyncScheduler
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.routers import contribuyentes, health, sync

OPENAPI_TAGS = [
    {
        "name": "Contribuyentes",
        "description": "Consultas exactas por RNC/Cédula y búsquedas por razón social.",
    },
    {
        "name": "Sincronización",
        "description": "Descarga y actualización masiva del padrón oficial de la DGII.",
    },
    {
        "name": "Sistema",
        "description": "Endpoints operativos y de monitoreo del servicio.",
    },
]


@dataclass(slots=True)
class AppContainer:
    """Contenedor de dependencias ensambladas para inyección en rutas."""

    conn_manager: PostgresConnectionManager
    scheduler: DailySyncScheduler
    init_uc: InitializeCatalogUseCase
    get_by_rnc_uc: GetContribuyenteByRncUseCase
    search_uc: SearchContribuyentesUseCase
    sync_uc: SyncRncCatalogUseCase
    health_uc: CheckHealthUseCase


def build_container(settings: Settings) -> AppContainer:
    """Ensambla infraestructura, casos de uso y cronjob (inversión de dependencias)."""
    conn_manager = PostgresConnectionManager(settings.database)
    repository = PostgresContribuyenteRepository(conn_manager)
    dgii_source = HttpDgiiZipSource(source_url=settings.sync.source_url)

    sync_uc = SyncRncCatalogUseCase(
        repository=repository,
        source=dgii_source,
        batch_size=settings.sync.batch_size,
    )
    init_uc = InitializeCatalogUseCase(repository=repository, sync_use_case=sync_uc)
    get_by_rnc_uc = GetContribuyenteByRncUseCase(repository=repository)
    search_uc = SearchContribuyentesUseCase(repository=repository)
    health_uc = CheckHealthUseCase(repository=repository)

    scheduler = DailySyncScheduler(
        hour=settings.sync.cron_hour,
        minute=settings.sync.cron_minute,
        job_fn=sync_uc.execute,
    )

    return AppContainer(
        conn_manager=conn_manager,
        scheduler=scheduler,
        init_uc=init_uc,
        get_by_rnc_uc=get_by_rnc_uc,
        search_uc=search_uc,
        sync_uc=sync_uc,
        health_uc=health_uc,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """Factory de la aplicación FastAPI con documentación Swagger/OpenAPI."""
    resolved_settings = settings or Settings.from_env()
    logging.basicConfig(
        level=resolved_settings.log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    container = build_container(resolved_settings)

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        fastapi_app.state.container = container
        container.init_uc.execute()
        container.scheduler.start()
        try:
            yield
        finally:
            container.scheduler.stop()
            container.conn_manager.close()

    fastapi_app = FastAPI(
        title=resolved_settings.app_title,
        summary="API REST para consultar y sincronizar el padrón RNC de la DGII.",
        description=(
            "Servicio construido con **Arquitectura Limpia** sobre FastAPI y PostgreSQL (`pg_trgm`) "
            "para consultar contribuyentes de la Dirección General de Impuestos Internos (DGII) "
            "de la República Dominicana.\n\n"
            "- **Swagger UI:** `/docs`\n"
            "- **ReDoc:** `/redoc`\n"
            "- **Esquema OpenAPI:** `/openapi.json`"
        ),
        version=resolved_settings.app_version,
        openapi_tags=OPENAPI_TAGS,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        swagger_ui_parameters={
            "defaultModelsExpandDepth": 2,
            "displayRequestDuration": True,
            "filter": True,
        },
        lifespan=lifespan,
    )
    fastapi_app.state.container = container

    register_exception_handlers(fastapi_app)
    fastapi_app.include_router(contribuyentes.router)
    fastapi_app.include_router(sync.router)
    fastapi_app.include_router(health.router)

    @fastapi_app.get("/", include_in_schema=False)
    @fastapi_app.get("/swagger", include_in_schema=False)
    def redirect_to_swagger() -> RedirectResponse:
        """Redirige la raíz y /swagger hacia Swagger UI (/docs)."""
        return RedirectResponse(url="/docs")

    return fastapi_app


app = create_app()
