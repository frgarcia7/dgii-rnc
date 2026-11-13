"""Funciones puras de lectura y parseo de archivos ZIP/CSV/TXT de la DGII."""

from __future__ import annotations

import csv
import io
import logging
import unicodedata
import zipfile
from typing import BinaryIO, Iterable, Iterator

from app.domain.entities import Contribuyente, normalize_rnc

logger = logging.getLogger(__name__)

HEADER_TOKENS = {"RNC", "CEDULA", "CÉDULA", "RNC/CEDULA", "CÉDULA/RNC"}

_HEADER_ALIASES: dict[str, set[str]] = {
    "rnc": {"RNC", "CEDULA", "RNC/CEDULA", "CEDULA/RNC", "RNC O CEDULA"},
    "razon_social": {
        "RAZON SOCIAL",
        "NOMBRE/RAZON SOCIAL",
        "NOMBRE O RAZON SOCIAL",
        "NOMBRE",
    },
    "actividad_economica": {"ACTIVIDAD ECONOMICA", "ACTIVIDAD"},
    "fecha_inicio_operaciones": {
        "FECHA DE INICIO OPERACIONES",
        "FECHA INICIO OPERACIONES",
        "FECHA CONSTITUCION",
    },
    "estatus": {"ESTADO", "ESTATUS"},
    "regimen": {"REGIMEN DE PAGO", "REGIMEN"},
}

_DEFAULT_CSV_COLS: dict[str, int] = {
    "rnc": 0,
    "razon_social": 1,
    "actividad_economica": 2,
    "fecha_inicio_operaciones": 3,
    "estatus": 4,
    "regimen": 5,
}


def _normalize_header(value: str) -> str:
    """Normaliza un nombre de columna eliminando tildes y espacios redundantes."""
    decomposed = unicodedata.normalize("NFKD", value)
    ascii_only = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(ascii_only.strip().upper().split())


def build_csv_column_map(row: list[str]) -> dict[str, int] | None:
    """Detecta una fila de encabezado CSV y construye el mapa columna->índice."""
    normalized_cells = [_normalize_header(cell) for cell in row]
    col_map: dict[str, int] = {}

    for idx, cell in enumerate(normalized_cells):
        for field_name, aliases in _HEADER_ALIASES.items():
            if cell in aliases and field_name not in col_map:
                col_map[field_name] = idx

    if "rnc" in col_map and "razon_social" in col_map:
        return col_map
    return None


def _get_col(row: list[str], index: int | None) -> str:
    if index is None or index < 0 or index >= len(row):
        return ""
    return row[index].strip()


def parse_csv_row(
    row: list[str], col_map: dict[str, int] | None = None
) -> Contribuyente | None:
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

    mapping = col_map or _DEFAULT_CSV_COLS
    raw_rnc = _get_col(row, mapping.get("rnc", 0))
    rnc = normalize_rnc(raw_rnc)[:20]
    if not rnc or rnc.upper() in HEADER_TOKENS:
        return None

    return Contribuyente(
        rnc=rnc,
        razon_social=_get_col(row, mapping.get("razon_social", 1)),
        actividad_economica=_get_col(row, mapping.get("actividad_economica")),
        fecha_inicio_operaciones=_get_col(
            row, mapping.get("fecha_inicio_operaciones")
        )[:50],
        estatus=_get_col(row, mapping.get("estatus")),
        regimen=_get_col(row, mapping.get("regimen")),
    )


def parse_pipe_line(line: str) -> Contribuyente | None:
    """Respaldo para el formato histórico DGII_RNC.TXT delimitado por '|'."""
    cleaned = line.rstrip("\r\n")
    if not cleaned:
        return None

    parts = cleaned.split("|")
    if len(parts) < 2:
        return None

    rnc = normalize_rnc(parts[0])[:20]
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


def detect_encoding_from_sample(sample: bytes) -> str:
    """Detecta UTF-8 (con o sin BOM) o ISO-8859-1 a partir de bytes iniciales."""
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        sample.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError:
        return "iso-8859-1"


def detect_encoding(zf: zipfile.ZipFile, member: str) -> str:
    """Detecta el encoding del archivo interno de un ZIP."""
    with zf.open(member, "r") as sample_file:
        sample = sample_file.read(8192)
    return detect_encoding_from_sample(sample)


def _iter_csv_entities(text_stream: io.TextIOWrapper) -> Iterator[Contribuyente | None]:
    """Itera filas CSV detectando encabezados dinámicos cuando existan."""
    reader = csv.reader(text_stream)
    col_map: dict[str, int] | None = None
    first_row = True

    for row in reader:
        if first_row:
            first_row = False
            detected = build_csv_column_map(row)
            if detected is not None:
                col_map = detected
                continue
        yield parse_csv_row(row, col_map=col_map)


def _yield_deduped_batches(
    entities: Iterable[Contribuyente | None], batch_size: int
) -> Iterator[list[Contribuyente]]:
    """Agrupa entidades en lotes deduplicados por RNC."""
    batch: dict[str, Contribuyente] = {}
    for entity in entities:
        if entity is None:
            continue

        batch[entity.rnc] = entity
        if len(batch) >= batch_size:
            yield list(batch.values())
            batch.clear()

    if batch:
        yield list(batch.values())
        batch.clear()


def _iter_text_stream_batches(
    raw_stream: BinaryIO, is_csv: bool, encoding: str, batch_size: int
) -> Iterator[list[Contribuyente]]:
    """Lee un stream binario de texto CSV o TXT y genera lotes deduplicados."""
    text_stream = io.TextIOWrapper(
        raw_stream, encoding=encoding, errors="replace", newline=""
    )
    try:
        entities = (
            _iter_csv_entities(text_stream)
            if is_csv
            else (parse_pipe_line(line) for line in text_stream)
        )
        yield from _yield_deduped_batches(entities, batch_size=batch_size)
    finally:
        # Evita cerrar raw_stream si lo administra el llamador (p. ej. ZipExtFile)
        text_stream.detach()


def iter_zip_batches(
    zip_buffer: BinaryIO, batch_size: int
) -> Iterator[list[Contribuyente]]:
    """Lee un ZIP (en memoria o archivo temporal) y produce lotes deduplicados."""
    zip_buffer.seek(0)
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
            yield from _iter_text_stream_batches(
                raw_file, is_csv=is_csv, encoding=encoding, batch_size=batch_size
            )


def iter_source_batches(
    source_stream: BinaryIO, batch_size: int, filename_hint: str = ""
) -> Iterator[list[Contribuyente]]:
    """Lee un archivo ZIP, CSV o TXT y produce lotes deduplicados por RNC."""
    source_stream.seek(0)
    if zipfile.is_zipfile(source_stream):
        yield from iter_zip_batches(source_stream, batch_size=batch_size)
        return

    source_stream.seek(0)
    sample = source_stream.read(8192)
    encoding = detect_encoding_from_sample(sample)
    source_stream.seek(0)

    hint = filename_hint.lower()
    if hint.endswith(".csv"):
        is_csv = True
    elif hint.endswith(".txt"):
        is_csv = False
    else:
        decoded_sample = sample.decode(encoding, errors="replace")
        is_csv = "|" not in decoded_sample and "," in decoded_sample

    logger.info(
        "Procesando archivo plano: %s (formato=%s, encoding=%s)",
        filename_hint or "<stream>",
        "CSV" if is_csv else "TXT",
        encoding,
    )
    yield from _iter_text_stream_batches(
        source_stream, is_csv=is_csv, encoding=encoding, batch_size=batch_size
    )
