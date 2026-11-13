"""Mapeo centralizado de excepciones de dominio a respuestas HTTP."""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from app.domain.exceptions import ContribuyenteNotFoundError, InvalidInputError


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

