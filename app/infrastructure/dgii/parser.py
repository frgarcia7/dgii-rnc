"""Funciones puras de lectura y parseo de archivos CSV/TXT dentro del ZIP de la DGII."""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from typing import Iterator

from app.domain.entities import Contribuyente

logger = logging.getLogger(__name__)

HEADER_TOKENS = {"RNC", "CEDULA", "CÉDULA", "RNC/CEDULA", "CÉDULA/RNC"}


def parse_csv_row(row: list[str]) -> Contribuyente | None:
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


def parse_pipe_line(line: str) -> Contribuyente | None:
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


def find_data_member(zf: zipfile.ZipFile) -> str:
    """Localiza el archivo CSV (preferido) o TXT dentro del ZIP."""
    members = [info.filename for info in zf.infolist() if not info.is_dir()]
    if not members:
        raise ValueError("El archivo ZIP de la DGII no contiene archivos.")

    for ext in (".csv", ".txt"):
        for name in members:
            if name.lower().endswith(ext):
                return name
    return members[0]


def detect_encoding(zf: zipfile.ZipFile, member: str) -> str:
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


def iter_zip_batches(
    zip_buffer: io.BytesIO, batch_size: int
) -> Iterator[list[Contribuyente]]:
    """Lee el ZIP en memoria y produce lotes deduplicados por RNC."""
    batch: dict[str, Contribuyente] = {}

    with zipfile.ZipFile(zip_buffer) as zf:
        target_member = find_data_member(zf)
        encoding = detect_encoding(zf, target_member)
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
                entities = (parse_csv_row(row) for row in csv.reader(text_stream))
            else:
                entities = (parse_pipe_line(line) for line in text_stream)

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

