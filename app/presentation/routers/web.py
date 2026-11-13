"""Router web para el buscador interno de escritorio/intranet y redirecciones."""

from __future__ import annotations

import base64
import hashlib
import re
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse

router = APIRouter(include_in_schema=False)

_INDEX_HTML_PATH = Path(__file__).resolve().parent.parent / "static" / "index.html"
_INDEX_HTML_CACHE: tuple[str, str] | None = None
_STYLE_RE = re.compile(r"<style>(.*?)</style>", re.DOTALL)
_SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.DOTALL)


def _hash_inline_block(content: str) -> str:
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    encoded = base64.b64encode(digest).decode("ascii")
    return f"'sha256-{encoded}'"


def _build_csp_header(html: str) -> str:
    style_hashes = " ".join(_hash_inline_block(m.group(1)) for m in _STYLE_RE.finditer(html))
    script_hashes = " ".join(
        _hash_inline_block(m.group(1)) for m in _SCRIPT_RE.finditer(html)
    )
    return (
        "default-src 'self'; "
        f"style-src 'self' {style_hashes}; "
        f"script-src 'self' {script_hashes}; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'"
    )


def _read_index_payload() -> tuple[str, str]:
    global _INDEX_HTML_CACHE
    if _INDEX_HTML_CACHE is None:
        html = _INDEX_HTML_PATH.read_text(encoding="utf-8")
        csp = _build_csp_header(html)
        _INDEX_HTML_CACHE = (html, csp)
    return _INDEX_HTML_CACHE


@router.get("/", response_class=HTMLResponse)
def web_home() -> HTMLResponse:
    """Sirve la interfaz web local de consulta y validación en lote."""
    html, csp = _read_index_payload()
    return HTMLResponse(
        content=html,
        headers={
            "Content-Security-Policy": csp,
            "Cache-Control": "no-cache",
        },
    )


@router.get("/swagger")
def redirect_to_swagger() -> RedirectResponse:
    """Redirige /swagger hacia Swagger UI (/docs)."""
    return RedirectResponse(url="/docs")
