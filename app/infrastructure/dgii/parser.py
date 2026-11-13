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

_ENCODING_CHUNK_BYTES = 65536
_MAX_ENCODING_SAMPLE_BYTES = 262144

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


def _is_valid_catalog_rnc(rnc: str) -> bool:
    """Verifica que el identificador normalizado sea numérico y de longitud válida."""
    return rnc.isascii() and rnc.isdigit() and 5 <= len(rnc) <= 20


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
    if not rnc or rnc.upper() in HEADER_TOKENS or not _is_valid_catalog_rnc(rnc):
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
    if not rnc or rnc.upper() in HEADER_TOKENS or not _is_valid_catalog_rnc(rnc):
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


def _read_encoding_sample(stream: BinaryIO) -> bytes:
    """Lee una muestra inicial suficiente (hasta 256 KB si empieza en ASCII puro)."""
    chunks: list[bytes] = []
    total = 0
    while total < _MAX_ENCODING_SAMPLE_BYTES:
        chunk = stream.read(_ENCODING_CHUNK_BYTES)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if not chunk.isascii():
            break
    return b"".join(chunks)


def detect_encoding_from_sample(sample: bytes) -> str:
    """Detecta UTF-8 (con o sin BOM) o Windows-1252 a partir de una muestra binaria."""
    if sample.startswith(b"\xef\xbb\xbf"):
        return "utf-8-sig"
    try:
        sample.decode("utf-8")
        return "utf-8"
    except UnicodeDecodeError as exc:
        if exc.reason == "unexpected end of data" and exc.start >= len(sample) - 4:
            try:
                sample[: exc.start].decode("utf-8")
                return "utf-8"
            except UnicodeDecodeError:
                pass
        return "cp1252"


def detect_encoding(zf: zipfile.ZipFile, member: str) -> str:
    """Detecta el encoding del archivo interno de un ZIP."""
    with zf.open(member, "r") as sample_file:
        sample = _read_encoding_sample(sample_file)
    return detect_encoding_from_sample(sample)


def detect_format_from_sample(
    sample: bytes, encoding: str, filename_hint: str = ""
) -> tuple[bool, str]:
    """Detecta si la fuente es tabular CSV (y su delimitador) o TXT heredado por '|'."""
    hint = filename_hint.lower()
    decoded = sample.decode(encoding, errors="replace")
    lines = [line.strip() for line in decoded.splitlines() if line.strip()][:10]

    if lines:
        first = lines[0]
        pipe_cols = first.split("|")
        semi_cols = first.split(";")
        comma_cols = first.split(",")

        if len(pipe_cols) >= 3:
            if build_csv_column_map(pipe_cols) is not None:
                return True, "|"
            return False, "|"

        if len(semi_cols) >= 3 and len(semi_cols) >= len(comma_cols):
            return True, ";"
        if len(comma_cols) >= 2:
            return True, ","

    if hint.endswith(".txt"):
        return False, "|"
    return True, ","


def _iter_csv_entities(
    text_stream: io.TextIOWrapper, delimiter: str = ","
) -> Iterator[Contribuyente | None]:
    """Itera filas CSV detectando encabezados dinámicos cuando existan."""
    reader = csv.reader(text_stream, delimiter=delimiter)
    col_map: dict[str, int] | None = None
    first_row = True

    for row in reader:
        if not row or all(not cell.strip() for cell in row):
            continue
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
    raw_stream: BinaryIO,
    is_csv: bool,
    encoding: str,
    batch_size: int,
    delimiter: str = ",",
) -> Iterator[list[Contribuyente]]:
    """Lee un stream binario de texto CSV o TXT y genera lotes deduplicados."""
    text_stream = io.TextIOWrapper(
        raw_stream, encoding=encoding, errors="replace", newline=""
    )
    try:
        entities = (
            _iter_csv_entities(text_stream, delimiter=delimiter)
            if is_csv
            else (parse_pipe_line(line) for line in text_stream)
        )
        yield from _yield_deduped_batches(entities, batch_size=batch_size)
    finally:
        # Evita cerrar raw_stream si lo administra el llamador (p. ej. ZipExtFile)
        text_stream.detach()


def _inspect_zip_member(
    zf: zipfile.ZipFile, target_member: str
) -> tuple[str, bool, str]:
    """Detecta encoding, modo CSV/TXT y delimitador del archivo interno."""
    with zf.open(target_member, "r") as sample_file:
        sample = _read_encoding_sample(sample_file)
    encoding = detect_encoding_from_sample(sample)
    is_csv, delimiter = detect_format_from_sample(
        sample, encoding=encoding, filename_hint=target_member
    )
    return encoding, is_csv, delimiter


def iter_zip_batches(
    zip_buffer: BinaryIO, batch_size: int
) -> Iterator[list[Contribuyente]]:
    """Lee un ZIP (en memoria o archivo temporal) y produce lotes deduplicados."""
    zip_buffer.seek(0)
    with zipfile.ZipFile(zip_buffer) as zf:
        target_member = find_data_member(zf)
        encoding, is_csv, delimiter = _inspect_zip_member(zf, target_member)
        logger.info(
            "Procesando archivo interno: %s (formato=%s, delim=%r, encoding=%s)",
            target_member,
            "CSV" if is_csv else "TXT",
            delimiter,
            encoding,
        )

        with zf.open(target_member, "r") as raw_file:
            yield from _iter_text_stream_batches(
                raw_file,
                is_csv=is_csv,
                encoding=encoding,
                batch_size=batch_size,
                delimiter=delimiter,
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
    sample = _read_encoding_sample(source_stream)
    encoding = detect_encoding_from_sample(sample)
    source_stream.seek(0)

    is_csv, delimiter = detect_format_from_sample(
        sample, encoding=encoding, filename_hint=filename_hint
    )

    logger.info(
        "Procesando archivo plano: %s (formato=%s, delim=%r, encoding=%s)",
        filename_hint or "<stream>",
        "CSV" if is_csv else "TXT",
        delimiter,
        encoding,
    )
    yield from _iter_text_stream_batches(
        source_stream,
        is_csv=is_csv,
        encoding=encoding,
        batch_size=batch_size,
        delimiter=delimiter,
    )
