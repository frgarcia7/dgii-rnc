"""Configuración centralizada de la aplicación basada en variables de entorno."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_DGII_URL = "https://dgii.gov.do/app/WebApps/Consultas/RNC/RNC_CONTRIBUYENTES.zip"


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


def _parse_bool(env_name: str, default: bool) -> bool:
    """Lee y valida un booleano desde variables de entorno."""
    raw = os.getenv(env_name)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on", "si", "sí"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"La variable {env_name}='{raw}' debe ser un booleano (true/false).")


def _parse_csv_list(env_name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Lee una lista separada por comas desde variables de entorno."""
    raw = os.getenv(env_name)
    if raw is None or not raw.strip():
        return default
    items = tuple(part.strip() for part in raw.split(",") if part.strip())
    return items or default


def _optional_str(env_name: str) -> str | None:
    """Retorna None si la variable no está definida o está vacía."""
    raw = os.getenv(env_name)
    if raw is None:
        return None
    cleaned = raw.strip()
    return cleaned or None


@dataclass(frozen=True, slots=True)
class DatabaseSettings:
    """Parámetros de conexión a PostgreSQL."""

    host: str
    port: int
    user: str
    password: str
    name: str
    pool_min: int = 1
    pool_max: int = 10
    statement_timeout_ms: int = 5_000


@dataclass(frozen=True, slots=True)
class SyncSettings:
    """Parámetros de descarga, archivo local y programación diaria de la DGII."""

    source_url: str = DEFAULT_DGII_URL
    batch_size: int = 10_000
    cron_hour: int = 3
    cron_minute: int = 0
    enable_scheduler: bool = True
    local_zip_path: str | None = None
    http_retries: int = 3
    http_connect_timeout: int = 15
    http_read_timeout: int = 180


@dataclass(frozen=True, slots=True)
class ApiSettings:
    """Parámetros HTTP, seguridad opcional y límites operativos."""

    cors_origins: tuple[str, ...] = ("*",)
    api_key: str | None = None
    sync_api_key: str | None = None
    enable_manual_sync: bool = True
    batch_max_items: int = 200
    cache_max_age_seconds: int = 300


@dataclass(frozen=True, slots=True)
class Settings:
    """Configuración raíz de la aplicación."""

    app_title: str
    app_version: str
    log_level: str
    database: DatabaseSettings
    sync: SyncSettings
    api: ApiSettings = ApiSettings()

    @classmethod
    def from_env(cls) -> Settings:
        """Construye la configuración leyendo las variables de entorno."""
        pool_min = _parse_int("DB_POOL_MIN", 1, 1, 50)
        pool_max = _parse_int("DB_POOL_MAX", 10, 1, 100)
        if pool_min > pool_max:
            raise ValueError(
                f"DB_POOL_MIN ({pool_min}) no puede ser mayor que DB_POOL_MAX ({pool_max})."
            )

        return cls(
            app_title=os.getenv("APP_TITLE", "DGII RNC API"),
            app_version=os.getenv("APP_VERSION", "1.1.0"),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            database=DatabaseSettings(
                host=os.getenv("DB_HOST", "postgres"),
                port=_parse_int("DB_PORT", 5432, 1, 65535),
                user=os.getenv("DB_USER", "dgii_user"),
                password=os.getenv("DB_PASSWORD", "dgii_password"),
                name=os.getenv("DB_NAME", "dgii_rnc"),
                pool_min=pool_min,
                pool_max=pool_max,
                statement_timeout_ms=_parse_int(
                    "DB_STATEMENT_TIMEOUT_MS", 5_000, 500, 60_000
                ),
            ),
            sync=SyncSettings(
                source_url=os.getenv("DGII_RNC_URL", DEFAULT_DGII_URL),
                batch_size=_parse_int("SYNC_BATCH_SIZE", 10_000, 500, 50_000),
                cron_hour=_parse_int("SYNC_HOUR", 3, 0, 23),
                cron_minute=_parse_int("SYNC_MINUTE", 0, 0, 59),
                enable_scheduler=_parse_bool("ENABLE_SCHEDULER", True),
                local_zip_path=_optional_str("DGII_LOCAL_ZIP_PATH"),
                http_retries=_parse_int("DGII_HTTP_RETRIES", 3, 0, 10),
                http_connect_timeout=_parse_int("DGII_HTTP_CONNECT_TIMEOUT", 15, 3, 120),
                http_read_timeout=_parse_int("DGII_HTTP_READ_TIMEOUT", 180, 10, 900),
            ),
            api=ApiSettings(
                cors_origins=_parse_csv_list("CORS_ORIGINS", ("*",)),
                api_key=_optional_str("API_KEY"),
                sync_api_key=_optional_str("SYNC_API_KEY"),
                enable_manual_sync=_parse_bool("ENABLE_MANUAL_SYNC", True),
                batch_max_items=_parse_int("BATCH_MAX_ITEMS", 200, 1, 1000),
                cache_max_age_seconds=_parse_int("CACHE_MAX_AGE_SECONDS", 300, 0, 86400),
            ),
        )
