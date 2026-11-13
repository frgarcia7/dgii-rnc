"""Puertos (interfaces) de dominio siguiendo inversión de dependencias."""

from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Protocol

from app.domain.entities import (
    CatalogStats,
    Contribuyente,
    DgiiFetchResult,
    SearchResult,
    SyncRun,
)


class ContribuyenteRepository(Protocol):
    """Contrato de persistencia, consulta y coordinación de sincronizaciones."""

    def init_schema(self) -> None:
        """Inicializa extensiones, tablas, funciones e índices necesarios."""
        ...

    def count(self) -> int:
        """Retorna el total de contribuyentes almacenados."""
        ...

    def has_records(self) -> bool:
        """Verifica rápidamente si existe al menos un contribuyente almacenado."""
        ...

    def get_by_rnc(self, rnc: str) -> Contribuyente | None:
        """Obtiene un contribuyente por su RNC exacto."""
        ...

    def get_by_rncs(self, rncs: list[str]) -> list[Contribuyente]:
        """Obtiene múltiples contribuyentes por una lista de RNCs exactos."""
        ...

    def search_by_razon_social(
        self,
        query: str,
        limit: int,
        offset: int,
        estatus: str | None = None,
        regimen: str | None = None,
        actividad: str | None = None,
    ) -> SearchResult:
        """Busca contribuyentes por razón social con filtros opcionales y total."""
        ...

    def upsert_batch(self, items: list[Contribuyente]) -> tuple[int, int]:
        """Inserta o actualiza un lote y retorna (procesados, modificados)."""
        ...

    def sync_lock(self) -> AbstractContextManager[bool]:
        """Intenta adquirir un bloqueo exclusivo entre procesos para sincronizar."""
        ...

    def is_sync_locked(self) -> bool:
        """Indica si algún proceso mantiene actualmente el bloqueo de sincronización."""
        ...

    def cleanup_stale_sync_runs(self) -> int:
        """Marca como interrumpidas las sincronizaciones 'running' huérfanas sin lock."""
        ...

    def analyze_catalog(self) -> None:
        """Actualiza estadísticas del planificador de consultas tras cambios masivos."""
        ...

    def get_last_successful_sync(self) -> SyncRun | None:
        """Retorna la última sincronización completada exitosamente."""
        ...

    def get_latest_sync(self) -> SyncRun | None:
        """Retorna la ejecución de sincronización más reciente (cualquier estado)."""
        ...

    def record_sync_start(self, source: str) -> int | None:
        """Registra el inicio de una sincronización y retorna su ID."""
        ...

    def record_sync_finish(
        self,
        run_id: int | None,
        status: str,
        processed: int,
        changed: int,
        elapsed_seconds: float,
        source: str | None = None,
        etag: str | None = None,
        content_hash: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Actualiza el registro de una sincronización al finalizar."""
        ...

    def get_stats(self, is_syncing: bool) -> CatalogStats:
        """Retorna estadísticas agregadas del padrón."""
        ...

    def is_healthy(self) -> bool:
        """Verifica la disponibilidad del repositorio."""
        ...


class DgiiSourcePort(Protocol):
    """Contrato para obtener lotes de contribuyentes desde la fuente oficial o local."""

    def fetch(
        self,
        batch_size: int,
        known_etag: str | None = None,
        known_hash: str | None = None,
        force: bool = False,
    ) -> DgiiFetchResult:
        """Obtiene el stream de lotes deduplicados si la fuente cambió o se fuerza."""
        ...
