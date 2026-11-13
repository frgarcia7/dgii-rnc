"""Entidades puras del dominio DGII RNC (sin dependencias de frameworks)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


def normalize_rnc(raw_rnc: str) -> str:
    """Normaliza un RNC o Cédula eliminando espacios y guiones."""
    return raw_rnc.strip().replace("-", "")


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
class SyncReport:
    """Resultado de una ejecución de sincronización del padrón."""

    status: str
    processed: int
    elapsed_seconds: float = 0.0
