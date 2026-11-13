"""Composition Root de la aplicación FastAPI (Arquitectura Limpia + Swagger/OpenAPI)."""

from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.application.cache import TtlLruCache
from app.application.use_cases import (
    BatchLookupRncUseCase,
    CheckHealthUseCase,
    GetContribuyenteByRncUseCase,
    GetContribuyenteHistoryUseCase,
    GetStatsUseCase,
    InitializeCatalogUseCase,
    SearchContribuyentesUseCase,
    SyncRncCatalogUseCase,
    ValidateRncUseCase,
)
from app.core.config import Settings
from app.domain.entities import CatalogStats, Contribuyente
from app.infrastructure.db.connection import PostgresConnectionManager
from app.infrastructure.db.postgres_repository import PostgresContribuyenteRepository
from app.infrastructure.dgii.http_zip_source import HttpDgiiZipSource
from app.infrastructure.scheduler.cron import DailySyncScheduler
from app.presentation.exception_handlers import register_exception_handlers
from app.presentation.rate_limit import SlidingWindowRateLimiter
from app.presentation.routers import contribuyentes, health, sync, web

logger = logging.getLogger(__name__)
_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

OPENAPI_TAGS = [
    {
        "name": "Contribuyentes",
        "description": (
            "Consultas exactas, validación de RNC/Cédula, lotes (606/607), búsqueda y estadísticas."
        ),
    },
    {
        "name": "Sincronización",
        "description": "Descarga, monitoreo y actualización masiva del padrón oficial de la DGII.",
    },
    {
        "name": "Sistema",
        "description": "Endpoints operativos y de monitoreo del servicio.",
    },
]


@dataclass(slots=True)
class AppContainer:
    """Contenedor de dependencias ensambladas para inyección en rutas."""

    conn_manager: PostgresConnectionManager | None
    scheduler: DailySyncScheduler | None
    init_uc: InitializeCatalogUseCase
    get_by_rnc_uc: GetContribuyenteByRncUseCase
    history_uc: GetContribuyenteHistoryUseCase
    validate_rnc_uc: ValidateRncUseCase
    batch_lookup_uc: BatchLookupRncUseCase
    search_uc: SearchContribuyentesUseCase
    stats_uc: GetStatsUseCase
    sync_uc: SyncRncCatalogUseCase
    health_uc: CheckHealthUseCase


def build_container(settings: Settings) -> AppContainer:
    """Ensambla infraestructura, cachés, casos de uso y cronjob."""
    conn_manager = PostgresConnectionManager(settings.database)
    repository = PostgresContribuyenteRepository(conn_manager)
    dgii_source = HttpDgiiZipSource(
        source_url=settings.sync.source_url,
        timeout=(settings.sync.http_connect_timeout, settings.sync.http_read_timeout),
        retries=settings.sync.http_retries,
        local_zip_path=settings.sync.local_zip_path,
    )

    lookup_cache: TtlLruCache[str, Contribuyente | None] = TtlLruCache(
        maxsize=settings.api.lookup_cache_size,
        ttl_seconds=settings.api.lookup_cache_ttl_seconds,
    )
    stats_cache: TtlLruCache[str, CatalogStats] = TtlLruCache(
        maxsize=1,
        ttl_seconds=settings.api.stats_cache_ttl_seconds,
    )
    count_cache: TtlLruCache[str, int] = TtlLruCache(
        maxsize=1,
        ttl_seconds=settings.api.stats_cache_ttl_seconds,
    )

    sync_uc = SyncRncCatalogUseCase(
        repository=repository,
        source=dgii_source,
        batch_size=settings.sync.batch_size,
        on_catalog_updated=(lookup_cache.clear, stats_cache.clear, count_cache.clear),
    )
    init_uc = InitializeCatalogUseCase(repository=repository, sync_use_case=sync_uc)
    get_by_rnc_uc = GetContribuyenteByRncUseCase(
        repository=repository, lookup_cache=lookup_cache
    )
    history_uc = GetContribuyenteHistoryUseCase(repository=repository)
    validate_rnc_uc = ValidateRncUseCase(
        repository=repository, lookup_cache=lookup_cache
    )
    batch_lookup_uc = BatchLookupRncUseCase(
        repository=repository, max_items=settings.api.batch_max_items
    )
    search_uc = SearchContribuyentesUseCase(repository=repository)
    stats_uc = GetStatsUseCase(
        repository=repository, sync_use_case=sync_uc, stats_cache=stats_cache
    )
    health_uc = CheckHealthUseCase(repository=repository, count_cache=count_cache)

    scheduler = (
        DailySyncScheduler(
            hour=settings.sync.cron_hour,
            minute=settings.sync.cron_minute,
            job_fn=sync_uc.execute,
        )
        if settings.sync.enable_scheduler
        else None
    )

    return AppContainer(
        conn_manager=conn_manager,
        scheduler=scheduler,
        init_uc=init_uc,
        get_by_rnc_uc=get_by_rnc_uc,
        history_uc=history_uc,
        validate_rnc_uc=validate_rnc_uc,
        batch_lookup_uc=batch_lookup_uc,
        search_uc=search_uc,
        stats_uc=stats_uc,
        sync_uc=sync_uc,
        health_uc=health_uc,
    )


def _log_startup_security_notices(settings: Settings) -> None:
    if settings.database.password == "dgii_password":
        logger.warning(
            "DB_PASSWORD usa el valor por defecto; cámbialo antes de exponer este servicio."
        )
    if settings.api.enable_manual_sync:
        if settings.api.allow_remote_sync:
            logger.warning(
                "ALLOW_REMOTE_SYNC=true: protege POST /api/v1/sync en tu proxy o gateway."
            )
        else:
            logger.info(
                "POST /api/v1/sync restringido a solicitudes locales (ALLOW_REMOTE_SYNC=false)."
            )


def create_app(
    settings: Settings | None = None,
    container: AppContainer | None = None,
    run_startup: bool = True,
) -> FastAPI:
    """Factory de la aplicación FastAPI con documentación Swagger/OpenAPI e interfaz web."""
    resolved_settings = settings or Settings.from_env()
    logging.basicConfig(
        level=resolved_settings.log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )

    resolved_container = container or build_container(resolved_settings)

    @asynccontextmanager
    async def lifespan(fastapi_app: FastAPI):
        fastapi_app.state.settings = resolved_settings
        fastapi_app.state.container = resolved_container
        if run_startup:
            _log_startup_security_notices(resolved_settings)
            resolved_container.init_uc.execute(run_async=True)
            if resolved_container.scheduler is not None:
                resolved_container.scheduler.start()
        try:
            yield
        finally:
            if resolved_container.scheduler is not None:
                resolved_container.scheduler.stop()
            if resolved_container.conn_manager is not None:
                resolved_container.conn_manager.close()

    fastapi_app = FastAPI(
        title=resolved_settings.app_title,
        summary="API REST y buscador web local para consultar el padrón RNC de la DGII.",
        description=(
            "Servicio self-hosted construido con **Arquitectura Limpia** sobre FastAPI y "
            "PostgreSQL (`pg_trgm` + `unaccent`) para consultar contribuyentes de la Dirección "
            "General de Impuestos Internos (DGII) de la República Dominicana.\n\n"
            "- **Buscador Web:** `/` (consulta individual y validación en lote 606/607)\n"
            "- **Swagger UI:** `/docs`\n"
            "- **ReDoc:** `/redoc`\n"
            "- **Esquema OpenAPI:** `/openapi.json`\n\n"
            "> Los endpoints bajo `/api/v1/...` representan la interfaz versionada recomendada; "
            "`/api/...` se mantiene como alias compatible."
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
    fastapi_app.state.settings = resolved_settings
    fastapi_app.state.container = resolved_container
    fastapi_app.state.rate_limiter = SlidingWindowRateLimiter()

    @fastapi_app.middleware("http")
    async def security_and_request_headers(request: Request, call_next) -> Response:
        incoming_req_id = request.headers.get("x-request-id", "").strip()
        request_id = (
            incoming_req_id
            if _REQUEST_ID_RE.match(incoming_req_id)
            else uuid.uuid4().hex
        )
        request.state.request_id = request_id

        try:
            response: Response = await call_next(request)
        except Exception as exc:
            logger.exception(
                "Error inesperado procesando %s %s (request_id=%s)",
                request.method,
                request.url.path,
                request_id,
                exc_info=exc,
            )
            response = JSONResponse(
                status_code=500,
                content={"detail": "Error interno del servidor."},
            )

        response.headers.setdefault("X-Request-ID", request_id)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Referrer-Policy", "strict-origin-when-cross-origin"
        )
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )

        rate_headers: dict[str, str] | None = getattr(
            request.state, "rate_limit_headers", None
        )
        if rate_headers:
            for key, value in rate_headers.items():
                response.headers.setdefault(key, value)

        return response

    fastapi_app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.api.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    register_exception_handlers(fastapi_app)

    # API versionada principal (/api/v1) y alias retrocompatible (/api)
    fastapi_app.include_router(contribuyentes.router, prefix="/api/v1")
    fastapi_app.include_router(sync.router, prefix="/api/v1")
    fastapi_app.include_router(
        contribuyentes.router, prefix="/api", include_in_schema=False
    )
    fastapi_app.include_router(sync.router, prefix="/api", include_in_schema=False)

    fastapi_app.include_router(health.router)
    fastapi_app.include_router(web.router)

    return fastapi_app


app = create_app()
