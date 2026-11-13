"""Configuración centralizada de la aplicación basada en variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _parse_int(env_name: str, default: int, min_val: int, max_val: int) -> int:
    """Lee y valida un entero desde variables de entorno."""
    raw = os.getenv(env_name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"La variable {env_name}='{raw}' debe ser un entero.") from exc
    if not (min_val <= value <= max_val):
        raise ValueError(
            f"La variable {env_name}={value} debe estar entre {min_val} y {max_val}."
        )
    return value


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Parámetros de conexión a PostgreSQL."""

    host: str
    port: int
    user: str
    password: str
    name: str
    pool_min: int
    pool_max: int


@dataclass(frozen=True, slots=True)
class SyncSettings:
    """Parámetros de descarga y programación diaria de la DGII."""

    source_url: str
    batch_size: int
    cron_hour: int
    cron_minute: int


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuración raíz de la aplicación."""

    app_title: str
    app_version: str
    log_level: str
    database: DatabaseSettings
    sync: SyncSettings

    @classmethod
    def from_env(cls) -> Settings:
        """Construye la configuración leyendo las variables de entorno."""
        return cls(
            app_title=os.getenv("APP_TITLE", "DGII RNC API"),
            app_version=os.getenv("APP_VERSION", "1.0.0"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            database=DatabaseSettings(
                host=os.getenv("DB_HOST", "postgres"),
                port=_parse_int("DB_PORT", 5432, 1, 65535),
                user=os.getenv("DB_USER", "dgii_user"),
                password=os.getenv("DB_PASSWORD", "dgii_password"),
                name=os.getenv("DB_NAME", "dgii_rnc"),
                pool_min=_parse_int("DB_POOL_MIN", 1, 1, 50),
                pool_max=_parse_int("DB_POOL_MAX", 10, 1, 100),
            ),
            sync=SyncSettings(
                source_url=os.getenv(
                    "DGII_RNC_URL",
                    "https://dgii.gov.do/app/WebApps/Consultas/RNC/RNC_CONTRIBUYENTES.zip",
                ),
                batch_size=_parse_int("SYNC_BATCH_SIZE", 10_000, 500, 50_000),
                cron_hour=_parse_int("SYNC_HOUR", 3, 0, 23),
                cron_minute=_parse_int("SYNC_MINUTE", 0, 0, 59),
            ),
        )

