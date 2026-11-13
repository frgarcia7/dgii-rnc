"""Descarga y lectura en memoria del padrón oficial CSV/TXT de la DGII."""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from typing import Iterator

import requests

from app.domain.entities import Contribuyente

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

HEADER_TOKENS = {"RNC", "CEDULA", "CÉDULA", "RNC/CEDULA", "CÉDULA/RNC"}


def _parse_csv_row(row: list[str]) -> Contribuyente | None:
    """
    Mapea una fila del CSV oficial RNC_Contribuyentes_*.csv:
      0: RNC
      1: RAZÓN SOCIAL
      2: ACTIVIDAD ECONÓMICA
      3: FECHA DE INICIO OPERACIONES
      4: ESTADO
      5: RÉGIMEN DE PAGO
    """
    if len(row) < 2:
        return None

    rnc = row[0].strip()[:20]
    if not rnc or rnc.upper() in HEADER_TOKENS:
        return None

    while len(row) < 6:
        row.append("")

    return Contribuyente(
        rnc=rnc,
        razon_social=row[1].strip(),
        actividad_economica=row[2].strip(),
        fecha_inicio_operaciones=row[3].strip()[:50],
        estatus=row[4].strip(),
        regimen=row[5].strip(),
    )


def _parse_pipe_line(line: str) -> Contribuyente | None:
    """Respaldo para el formato histórico DGII_RNC.TXT delimitado por '|'."""
    cleaned = line.rstrip("\r\n")
    if not cleaned:
        return None

    parts = cleaned.split("|")
    if len(parts) < 2:
        return None

    rnc = parts[0].strip()[:20]
    if not rnc or rnc.upper() in HEADER_TOKENS:
        return None

    while len(parts) < 11:
        parts.append("")

    return Contribuyente(
        rnc=rnc,
        razon_social=parts[1].strip(),
        actividad_economica=parts[3].strip(),
        fecha_inicio_operaciones=parts[8].strip()[:50],
        estatus=parts[9].strip(),
        regimen=parts[10].strip(),
    )


def _find_data_member(zf: zipfile.ZipFile) -> str:
    """Localiza el archivo CSV (preferido) o TXT dentro del ZIP."""
    members = [info.filename for info in zf.infolist() if not info.is_dir()]
    if not members:
        raise ValueError("El archivo ZIP de la DGII no contiene archivos.")

    for ext in (".csv", ".txt"):
        for name in members:
            if name.lower().endswith(ext):
                return name
    return members[0]


def _detect_encoding(zf: zipfile.ZipFile, member: str) -> str:
    """Detecta UTF-8 (con o sin BOM) o ISO-8859-1 a partir de una muestra inicial."""
    with zf.open(member, "r") as sample_file:
        sample = sample_file.read(8192)

    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        sample.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "iso-8859-1"


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
        batch: dict[str, Contribuyente] = {}

        with zipfile.ZipFile(zip_buffer) as zf:
            target_member = _find_data_member(zf)
            encoding = _detect_encoding(zf, target_member)
            is_csv = target_member.lower().endswith(".csv")
            logger.info(
                "Procesando archivo interno: %s (formato=%s, encoding=%s)",
                target_member,
                "CSV" if is_csv else "TXT",
                encoding,
            )

            with zf.open(target_member, "r") as raw_file:
                text_stream = io.TextIOWrapper(
                    raw_file, encoding=encoding, errors="replace", newline=""
                )
                if is_csv:
                    entities = (
                        _parse_csv_row(row) for row in csv.reader(text_stream)
                    )
                else:
                    entities = (_parse_pipe_line(line) for line in text_stream)

                for entity in entities:
                    if entity is None:
                        continue

                    # Diccionario por lote para deduplicar RNCs repetidos en el mismo batch
                    batch[entity.rnc] = entity
                    if len(batch) >= batch_size:
                        yield list(batch.values())
                        batch.clear()

        if batch:
            yield list(batch.values())
            batch.clear()
