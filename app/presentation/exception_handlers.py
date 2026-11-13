"""Mapeo centralizado de excepciones de dominio e infraestructura a HTTP."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.domain.exceptions import (
    ContribuyenteNotFoundError,
    DatabaseTimeoutError,
    DatabaseUnavailableError,
    InvalidInputError,
    SyncAlreadyRunningError,
)

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """Registra los manejadores globales de excepciones del dominio."""

    @app.exception_handler(ContribuyenteNotFoundError)
    async def handle_not_found(
        _request: Request, exc: ContribuyenteNotFoundError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )

    @app.exception_handler(InvalidInputError)
    async def handle_invalid_input(
        _request: Request, exc: InvalidInputError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": str(exc)},
        )

    @app.exception_handler(SyncAlreadyRunningError)
    async def handle_sync_already_running(
        _request: Request, exc: SyncAlreadyRunningError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(DatabaseTimeoutError)
    async def handle_database_timeout(
        _request: Request, exc: DatabaseTimeoutError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            content={"detail": str(exc)},
        )

    @app.exception_handler(DatabaseUnavailableError)
    async def handle_database_unavailable(
        _request: Request, exc: DatabaseUnavailableError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": str(exc)},
            headers={"Retry-After": "5"},
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(
        request: Request, exc: Exception
    ) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception(
            "Error inesperado procesando %s %s (request_id=%s)",
            request.method,
            request.url.path,
            request_id,
            exc_info=exc,
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Error interno del servidor."},
        )
