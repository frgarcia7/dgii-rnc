"""Router web para el buscador interno de escritorio/intranet y redirecciones."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(include_in_schema=False)

_INDEX_HTML_PATH = Path(__file__).resolve().parent.parent / "static" / "index.html"
_INDEX_HTML_CACHE: str | None = None


def _read_index_html() -> str:
    global _INDEX_HTML_CACHE
    if _INDEX_HTML_CACHE is None:
        _INDEX_HTML_CACHE = _INDEX_HTML_PATH.read_text(encoding="utf-8")
    return _INDEX_HTML_CACHE


@router.get("/", response_class=HTMLResponse)
def web_home() -> HTMLResponse:
    """Sirve la interfaz web interna de consulta y validación en lote."""
    return HTMLResponse(content=_read_index_html())


@router.get("/swagger")
def redirect_to_swagger() -> RedirectResponse:
    """Redirige /swagger hacia Swagger UI (/docs)."""
    return RedirectResponse(url="/docs")

