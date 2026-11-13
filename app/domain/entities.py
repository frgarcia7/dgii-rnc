"""Entidades puras y reglas del dominio DGII RNC (sin dependencias de frameworks)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterator

_RNC_WEIGHTS = (7, 9, 8, 6, 5, 4, 3, 2)
_CLEAN_PATTERN = re.compile(r"[\s-]+")


def normalize_rnc(raw_rnc: str) -> str:
    """Normaliza un RNC o Cédula eliminando espacios y guiones."""
    return _CLEAN_PATTERN.sub("", raw_rnc)


def detect_rnc_type(rnc: str) -> str:
    """Determina si un identificador normalizado es RNC (9), CEDULA (11) o DESCONOCIDO."""
    if rnc.isascii() and rnc.isdigit():
        if len(rnc) == 9:
            return "RNC"
        if len(rnc) == 11:
            return "CEDULA"
    return "DESCONOCIDO"


def format_rnc(rnc: str) -> str:
    """Da formato legible dominicano a un RNC (XXX-XXXXX-X) o Cédula (XXX-XXXXXXX-X)."""
    normalized = normalize_rnc(rnc)
    tipo = detect_rnc_type(normalized)
    if tipo == "RNC":
        return f"{normalized[:3]}-{normalized[3:8]}-{normalized[8]}"
    if tipo == "CEDULA":
        return f"{normalized[:3]}-{normalized[3:10]}-{normalized[10]}"
    return normalized


def is_valid_rnc_check_digit(rnc: str) -> bool:
    """Valida el dígito verificador de un RNC de 9 dígitos (algoritmo Módulo 11 DGII)."""
    if not (rnc.isascii() and rnc.isdigit() and len(rnc) == 9) or rnc == "000000000":
        return False

    total = sum(int(digit) * weight for digit, weight in zip(rnc[:8], _RNC_WEIGHTS))
    remainder = total % 11
    if remainder == 0:
        expected = 2
    elif remainder == 1:
        expected = 1
    else:
        expected = 11 - remainder
    return int(rnc[8]) == expected


def is_valid_cedula_check_digit(cedula: str) -> bool:
    """Valida el dígito verificador de una Cédula de 11 dígitos (Módulo 10 / Luhn JCE)."""
    if (
        not (cedula.isascii() and cedula.isdigit() and len(cedula) == 11)
        or cedula == "00000000000"
    ):
        return False

    total = 0
    for idx, char in enumerate(cedula[:10]):
        weight = 1 if idx % 2 == 0 else 2
        product = int(char) * weight
        total += (product // 10) + (product % 10)

    expected = (10 - (total % 10)) % 10
    return int(cedula[10]) == expected


@dataclass(frozen=True, slots=True)
class RncValidation:
    """Resultado de validar sintáctica y algorítmicamente un RNC o Cédula."""

    rnc: str
    rnc_formateado: str
    tipo: str
    formato_valido: bool
    digito_verificador_valido: bool


def validate_rnc_format(raw_rnc: str) -> RncValidation:
    """Normaliza y valida tipo y dígito verificador de un RNC o Cédula dominicana."""
    normalized = normalize_rnc(raw_rnc)
    tipo = detect_rnc_type(normalized)
    formato_valido = tipo in {"RNC", "CEDULA"}
    if tipo == "RNC":
        dv_valid = is_valid_rnc_check_digit(normalized)
    elif tipo == "CEDULA":
        dv_valid = is_valid_cedula_check_digit(normalized)
    else:
        dv_valid = False

    formatted = format_rnc(normalized) if formato_valido else normalized
    return RncValidation(
        rnc=normalized,
        rnc_formateado=formatted,
        tipo=tipo,
        formato_valido=formato_valido,
        digito_verificador_valido=dv_valid,
    )


@dataclass(frozen=True, slots=True)
class Contribuyente:
    """Representa un contribuyente registrado en el padrón de la DGII."""

    rnc: str
    razon_social: str
    actividad_economica: str
    fecha_inicio_operaciones: str
    estatus: str
    regimen: str
    updated_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Resultado paginado de búsqueda con conteo total de coincidencias."""

    items: list[Contribuyente]
    total: int


@dataclass(frozen=True, slots=True)
class BatchLookupResult:
    """Resultado de consulta masiva por lista de RNCs/Cédulas."""

    results: list[Contribuyente]
    not_found: list[str]
    invalid: list[str]


@dataclass(frozen=True, slots=True)
class SyncRun:
    """Registro histórico de una ejecución de sincronización."""

    id: int | None
    status: str
    source: str
    processed: int
    changed: int
    elapsed_seconds: float
    etag: str | None = None
    content_hash: str | None = None
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class SyncReport:
    """Resultado de una ejecución de sincronización del padrón."""

    status: str
    processed: int
    changed: int = 0
    elapsed_seconds: float = 0.0
    message: str = ""


@dataclass(frozen=True, slots=True)
class CatalogStats:
    """Métricas agregadas del padrón de contribuyentes y última sincronización."""

    total_contribuyentes: int
    is_syncing: bool
    by_estatus: dict[str, int]
    by_regimen: dict[str, int]
    last_sync: SyncRun | None = None


@dataclass(slots=True)
class DgiiFetchResult:
    """Stream de lotes de la fuente DGII con metadatos de caché y cierre seguro."""

    not_modified: bool
    source_label: str
    etag: str | None = None
    content_hash: str | None = None
    batches: Iterator[list[Contribuyente]] = field(default_factory=lambda: iter(()))
    _closer: Callable[[], None] | None = None

    def close(self) -> None:
        """Libera recursos temporales asociados al stream."""
        if self._closer is not None:
            closer = self._closer
            self._closer = None
            closer()

    def __enter__(self) -> DgiiFetchResult:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()
