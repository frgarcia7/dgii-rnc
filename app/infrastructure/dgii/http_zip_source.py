"""Descarga HTTP en memoria del padrón oficial CSV/TXT de la DGII."""

from __future__ import annotations

import io
import logging
import zipfile
from typing import Iterator

import requests

from app.domain.entities import Contribuyente
from app.infrastructure.dgii.parser import iter_zip_batches

logger = logging.getLogger(__name__)

OFFICIAL_FALLBACK_URLS = (
    "https://dgii.gov.do/app/WebApps/Consultas/RNC/RNC_CONTRIBUYENTES.zip",
    "https://www.dgii.gov.do/app/WebApps/Consultas/RNC/RNC_CONTRIBUYENTES.zip",
    "https://www.dgii.gov.do/app/WebApps/Consultas/RNC/DGII_RNC.zip",
)

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/zip,application/octet-stream,*/*;q=0.8",
    "Accept-Language": "es-DO,es;q=0.9,en;q=0.8",
    "Referer": "https://dgii.gov.do/",
    "Connection": "keep-alive",
}


class HttpDgiiZipSource:
    """Implementación de DgiiSourcePort usando requests, BytesIO y zipfile."""

    def __init__(self, source_url: str, timeout: tuple[int, int] = (15, 180)) -> None:
        self._source_url = source_url
        self._timeout = timeout

    def _download_zip_bytes(self) -> io.BytesIO:
        """Descarga el ZIP validando la firma binaria real y probando URLs oficiales."""
        candidates = [self._source_url] + [
            url for url in OFFICIAL_FALLBACK_URLS if url != self._source_url
        ]

        last_error: str = "No se pudo descargar un archivo ZIP válido."
        with requests.Session() as session:
            session.headers.update(BROWSER_HEADERS)

            for url in candidates:
                logger.info("Intentando descarga del padrón RNC desde %s", url)
                try:
                    response = session.get(url, timeout=self._timeout)
                    response.raise_for_status()
                except requests.RequestException as exc:
                    last_error = f"Error HTTP en {url}: {exc}"
                    logger.warning(last_error)
                    continue

                buffer = io.BytesIO(response.content)
                if zipfile.is_zipfile(buffer):
                    buffer.seek(0)
                    logger.info(
                        "Descarga ZIP válida desde %s (%.2f MB).",
                        url,
                        len(response.content) / (1024 * 1024),
                    )
                    return buffer

                content_type = response.headers.get("Content-Type", "desconocido")
                preview = response.text[:250].replace("\n", " ").strip()
                last_error = (
                    f"La respuesta de {url} no es un ZIP válido "
                    f"(status={response.status_code}, content-type={content_type}, "
                    f"inicio='{preview}')"
                )
                logger.warning(last_error)

        raise ValueError(last_error)

    def stream_batches(self, batch_size: int) -> Iterator[list[Contribuyente]]:
        """Descarga el ZIP en memoria y genera lotes deduplicados por RNC."""
        zip_buffer = self._download_zip_bytes()
        yield from iter_zip_batches(zip_buffer, batch_size=batch_size)
