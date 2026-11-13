"""Fuente de datos DGII vía descarga HTTP en streaming o archivo local."""

from __future__ import annotations

import hashlib
import logging
import tempfile
import zipfile
from pathlib import Path
from typing import BinaryIO

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from app.domain.entities import DgiiFetchResult
from app.infrastructure.dgii.parser import iter_source_batches

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

_CHUNK_SIZE = 64 * 1024
_SPOOL_MEMORY_LIMIT = 16 * 1024 * 1024


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


class HttpDgiiZipSource:
    """Implementación de DgiiSourcePort con streaming HTTP, caché ETag/SHA-256 y modo local."""

    def __init__(
        self,
        source_url: str,
        timeout: tuple[int, int] = (15, 180),
        retries: int = 3,
        local_zip_path: str | None = None,
    ) -> None:
        self._source_url = source_url
        self._timeout = timeout
        self._retries = retries
        self._local_zip_path = local_zip_path

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(BROWSER_HEADERS)
        if self._retries > 0:
            retry_cfg = Retry(
                total=self._retries,
                connect=self._retries,
                read=self._retries,
                backoff_factor=1.5,
                status_forcelist=(429, 500, 502, 503, 504),
                allowed_methods=frozenset({"GET", "HEAD"}),
                raise_on_status=False,
            )
            adapter = HTTPAdapter(max_retries=retry_cfg)
            session.mount("https://", adapter)
            session.mount("http://", adapter)
        return session

    def _fetch_from_local_file(
        self,
        local_path: Path,
        batch_size: int,
        known_hash: str | None,
        force: bool,
    ) -> DgiiFetchResult:
        if not local_path.is_file():
            raise FileNotFoundError(
                f"El archivo local configurado DGII_LOCAL_ZIP_PATH='{local_path}' no existe."
            )

        digest = _sha256_file(local_path)
        label = f"file://{local_path}"
        if not force and known_hash and digest == known_hash:
            logger.info("Archivo local sin cambios (sha256=%s).", digest[:12])
            return DgiiFetchResult(
                not_modified=True,
                source_label=label,
                content_hash=digest,
            )

        fh: BinaryIO = local_path.open("rb")
        try:
            batches = iter_source_batches(
                fh, batch_size=batch_size, filename_hint=local_path.name
            )
        except Exception:
            fh.close()
            raise

        logger.info("Cargando padrón desde archivo local: %s", local_path)
        return DgiiFetchResult(
            not_modified=False,
            source_label=label,
            content_hash=digest,
            batches=batches,
            _closer=fh.close,
        )

    def fetch(
        self,
        batch_size: int,
        known_etag: str | None = None,
        known_hash: str | None = None,
        force: bool = False,
    ) -> DgiiFetchResult:
        """Descarga en streaming o lee archivo local, omitiendo si no hubo cambios."""
        if self._local_zip_path:
            return self._fetch_from_local_file(
                Path(self._local_zip_path),
                batch_size=batch_size,
                known_hash=known_hash,
                force=force,
            )

        candidates = [self._source_url] + [
            url for url in OFFICIAL_FALLBACK_URLS if url != self._source_url
        ]
        last_error = "No se pudo descargar un archivo ZIP válido."

        with self._build_session() as session:
            for url in candidates:
                logger.info("Intentando descarga del padrón RNC desde %s", url)
                req_headers: dict[str, str] = {}
                if not force and known_etag and url == self._source_url:
                    req_headers["If-None-Match"] = known_etag

                try:
                    response = session.get(
                        url, headers=req_headers, timeout=self._timeout, stream=True
                    )
                except requests.RequestException as exc:
                    last_error = f"Error HTTP en {url}: {exc}"
                    logger.warning(last_error)
                    continue

                with response:
                    if response.status_code == 304:
                        logger.info("Fuente sin cambios según ETag (304 Not Modified).")
                        return DgiiFetchResult(
                            not_modified=True,
                            source_label=url,
                            etag=known_etag,
                            content_hash=known_hash,
                        )

                    try:
                        response.raise_for_status()
                    except requests.HTTPError as exc:
                        last_error = f"Error HTTP {response.status_code} en {url}: {exc}"
                        logger.warning(last_error)
                        continue

                    spool = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MEMORY_LIMIT)
                    hasher = hashlib.sha256()
                    total_bytes = 0
                    try:
                        for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                            if not chunk:
                                continue
                            spool.write(chunk)
                            hasher.update(chunk)
                            total_bytes += len(chunk)

                        spool.seek(0)
                        if not zipfile.is_zipfile(spool):
                            spool.seek(0)
                            preview = (
                                spool.read(250)
                                .decode("utf-8", errors="replace")
                                .replace("\n", " ")
                                .strip()
                            )
                            content_type = response.headers.get(
                                "Content-Type", "desconocido"
                            )
                            last_error = (
                                f"La respuesta de {url} no es un ZIP válido "
                                f"(status={response.status_code}, content-type={content_type}, "
                                f"inicio='{preview}')"
                            )
                            logger.warning(last_error)
                            spool.close()
                            continue

                        digest = hasher.hexdigest()
                        etag = response.headers.get("ETag")
                        if not force and known_hash and digest == known_hash:
                            spool.close()
                            logger.info(
                                "ZIP descargado idéntico al último procesado (sha256=%s).",
                                digest[:12],
                            )
                            return DgiiFetchResult(
                                not_modified=True,
                                source_label=url,
                                etag=etag or known_etag,
                                content_hash=digest,
                            )

                        spool.seek(0)
                        logger.info(
                            "Descarga ZIP válida desde %s (%.2f MB).",
                            url,
                            total_bytes / (1024 * 1024),
                        )
                        batches = iter_source_batches(
                            spool, batch_size=batch_size, filename_hint=url
                        )
                        return DgiiFetchResult(
                            not_modified=False,
                            source_label=url,
                            etag=etag,
                            content_hash=digest,
                            batches=batches,
                            _closer=spool.close,
                        )
                    except Exception:
                        spool.close()
                        raise

        raise ValueError(last_error)
