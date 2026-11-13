"""Casos de uso de la aplicación (orquestan dominio sin conocer frameworks ni SQL)."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import replace

from app.application.cache import TtlLruCache
from app.domain.entities import (
    BatchLookupResult,
    CatalogStats,
    Contribuyente,
    RncValidation,
    SearchResult,
    SyncReport,
    SyncRun,
    normalize_rnc,
    validate_rnc_format,
)
from app.domain.exceptions import ContribuyenteNotFoundError, InvalidInputError
from app.domain.ports import ContribuyenteRepository, DgiiSourcePort

logger = logging.getLogger(__name__)


def _is_valid_lookup_rnc(normalized: str) -> bool:
    """Valida que el identificador normalizado sea numérico y de longitud razonable."""
    return (
        bool(normalized)
        and normalized.isascii()
        and normalized.isdigit()
        and 5 <= len(normalized) <= 20
    )


class GetContribuyenteByRncUseCase:
    """Caso de uso para consultar un contribuyente exacto por RNC o Cédula."""

    def __init__(
        self,
        repository: ContribuyenteRepository,
        lookup_cache: TtlLruCache[str, Contribuyente | None] | None = None,
    ) -> None:
        self._repository = repository
        self._lookup_cache = lookup_cache

    def execute(self, raw_rnc: str) -> Contribuyente:
        normalized = normalize_rnc(raw_rnc)
        if not _is_valid_lookup_rnc(normalized):
            raise InvalidInputError(
                "El RNC o Cédula debe contener únicamente entre 5 y 20 dígitos."
            )

        contribuyente: Contribuyente | None = None
        hit = False
        if self._lookup_cache is not None:
            hit, contribuyente = self._lookup_cache.get_entry(normalized)

        if not hit:
            contribuyente = self._repository.get_by_rnc(normalized)
            if self._lookup_cache is not None:
                self._lookup_cache.set(normalized, contribuyente)

        if contribuyente is None:
            raise ContribuyenteNotFoundError(normalized)

        return contribuyente


class ValidateRncUseCase:
    """Caso de uso para validar formato/dígito verificador y presencia en padrón."""

    def __init__(
        self,
        repository: ContribuyenteRepository,
        lookup_cache: TtlLruCache[str, Contribuyente | None] | None = None,
    ) -> None:
        self._repository = repository
        self._lookup_cache = lookup_cache

    def execute(self, raw_rnc: str) -> tuple[RncValidation, Contribuyente | None]:
        validation = validate_rnc_format(raw_rnc)
        if not validation.rnc:
            raise InvalidInputError("Debe proporcionar un RNC o Cédula para validar.")

        contribuyente: Contribuyente | None = None
        if _is_valid_lookup_rnc(validation.rnc):
            hit = False
            if self._lookup_cache is not None:
                hit, contribuyente = self._lookup_cache.get_entry(validation.rnc)
            if not hit:
                contribuyente = self._repository.get_by_rnc(validation.rnc)
                if self._lookup_cache is not None:
                    self._lookup_cache.set(validation.rnc, contribuyente)

        return validation, contribuyente


class BatchLookupRncUseCase:
    """Caso de uso para consultar múltiples RNCs/Cédulas en una sola operación."""

    def __init__(
        self, repository: ContribuyenteRepository, max_items: int = 200
    ) -> None:
        self._repository = repository
        self._max_items = max_items

    def execute(self, raw_rncs: list[str]) -> BatchLookupResult:
        if not raw_rncs:
            raise InvalidInputError("Debe enviar al menos un RNC o Cédula.")
        if len(raw_rncs) > self._max_items:
            raise InvalidInputError(
                f"El máximo permitido por consulta en lote es {self._max_items} elementos."
            )

        valid_ordered: list[str] = []
        seen_valid: set[str] = set()
        invalid: list[str] = []

        for raw in raw_rncs:
            normalized = normalize_rnc(raw)
            if not _is_valid_lookup_rnc(normalized):
                invalid.append(raw)
                continue
            if normalized not in seen_valid:
                seen_valid.add(normalized)
                valid_ordered.append(normalized)

        found_items = (
            self._repository.get_by_rncs(valid_ordered) if valid_ordered else []
        )
        by_rnc = {item.rnc: item for item in found_items}

        results = [by_rnc[rnc] for rnc in valid_ordered if rnc in by_rnc]
        not_found = [rnc for rnc in valid_ordered if rnc not in by_rnc]
        return BatchLookupResult(results=results, not_found=not_found, invalid=invalid)


class SearchContribuyentesUseCase:
    """Caso de uso para buscar contribuyentes por coincidencia parcial en razón social."""

    def __init__(self, repository: ContribuyenteRepository) -> None:
        self._repository = repository

    def execute(
        self,
        raw_query: str,
        limit: int = 50,
        offset: int = 0,
        estatus: str | None = None,
        regimen: str | None = None,
        actividad: str | None = None,
    ) -> SearchResult:
        cleaned = " ".join(raw_query.split())
        if len(cleaned) < 2:
            raise InvalidInputError(
                "El parámetro 'query' debe tener al menos 2 caracteres."
            )

        clean_estatus = estatus.strip().upper() if estatus and estatus.strip() else None
        clean_regimen = regimen.strip().upper() if regimen and regimen.strip() else None
        clean_actividad = (
            " ".join(actividad.split()) if actividad and actividad.strip() else None
        )

        return self._repository.search_by_razon_social(
            query=cleaned,
            limit=limit,
            offset=offset,
            estatus=clean_estatus,
            regimen=clean_regimen,
            actividad=clean_actividad,
        )


class SyncRncCatalogUseCase:
    """Caso de uso para descargar e impactar por lotes el padrón RNC de la DGII."""

    def __init__(
        self,
        repository: ContribuyenteRepository,
        source: DgiiSourcePort,
        batch_size: int = 10_000,
        on_catalog_updated: Sequence[Callable[[], None]] = (),
    ) -> None:
        self._repository = repository
        self._source = source
        self._batch_size = batch_size
        self._local_lock = threading.Lock()
        self._on_catalog_updated = tuple(on_catalog_updated)

    def is_running(self) -> bool:
        """Indica si existe una sincronización en progreso en este proceso u otro worker."""
        if self._local_lock.locked():
            return True
        try:
            return self._repository.is_sync_locked()
        except Exception:
            return False

    def get_latest_run(self) -> SyncRun | None:
        """Obtiene la última ejecución de sincronización registrada."""
        return self._repository.get_latest_sync()

    def execute(self, force: bool = False) -> SyncReport:
        """Ejecuta la sincronización completa evitando ejecuciones concurrentes."""
        if not self._local_lock.acquire(blocking=False):
            logger.warning("Se omitió la sincronización porque ya hay una en curso.")
            return SyncReport(
                status="already_running",
                processed=0,
                message="Ya existe una sincronización en curso.",
            )

        try:
            with self._repository.sync_lock() as acquired:
                if not acquired:
                    logger.warning(
                        "Otro proceso mantiene el bloqueo de sincronización en PostgreSQL."
                    )
                    return SyncReport(
                        status="already_running",
                        processed=0,
                        message="Otro worker/proceso está sincronizando el padrón.",
                    )

                return self._run_sync(force=force)
        finally:
            self._local_lock.release()

    def _run_sync(self, force: bool) -> SyncReport:
        start = time.monotonic()
        last_ok = None if force else self._repository.get_last_successful_sync()
        known_etag = last_ok.etag if last_ok else None
        known_hash = last_ok.content_hash if last_ok else None

        run_id = self._repository.record_sync_start("dgii")
        total_processed = 0
        total_changed = 0

        try:
            with self._source.fetch(
                batch_size=self._batch_size,
                known_etag=known_etag,
                known_hash=known_hash,
                force=force,
            ) as fetch_result:
                if fetch_result.not_modified:
                    elapsed = round(time.monotonic() - start, 2)
                    self._safe_finish_record(
                        run_id=run_id,
                        status="up_to_date",
                        processed=0,
                        changed=0,
                        elapsed_seconds=elapsed,
                        source=fetch_result.source_label,
                        etag=fetch_result.etag,
                        content_hash=fetch_result.content_hash,
                    )
                    logger.info(
                        "Padrón sin cambios desde la última sincronización (%.2f s).",
                        elapsed,
                    )
                    return SyncReport(
                        status="up_to_date",
                        processed=0,
                        changed=0,
                        elapsed_seconds=elapsed,
                        message="El padrón ya estaba actualizado (sin cambios en la fuente).",
                    )

                for batch in fetch_result.batches:
                    processed, changed = self._repository.upsert_batch(batch)
                    total_processed += processed
                    total_changed += changed
                    if total_processed > 0 and total_processed % 100_000 == 0:
                        logger.info(
                            "Registros procesados: %d (modificados: %d)...",
                            total_processed,
                            total_changed,
                        )

                if total_processed == 0:
                    raise ValueError(
                        "El archivo descargado de la DGII no contiene registros válidos; "
                        "se abortó para proteger el catálogo actual."
                    )

                if total_changed > 0:
                    try:
                        self._repository.analyze_catalog()
                    except Exception:
                        logger.warning(
                            "No se pudo ejecutar ANALYZE tras la sincronización.",
                            exc_info=True,
                        )
                    self._invalidate_caches()

                elapsed = round(time.monotonic() - start, 2)
                self._safe_finish_record(
                    run_id=run_id,
                    status="completed",
                    processed=total_processed,
                    changed=total_changed,
                    elapsed_seconds=elapsed,
                    source=fetch_result.source_label,
                    etag=fetch_result.etag,
                    content_hash=fetch_result.content_hash,
                )
                logger.info(
                    "Sincronización completada: %d procesados, %d actualizados/nuevos en %.2f s.",
                    total_processed,
                    total_changed,
                    elapsed,
                )
                return SyncReport(
                    status="completed",
                    processed=total_processed,
                    changed=total_changed,
                    elapsed_seconds=elapsed,
                    message="Sincronización completada exitosamente.",
                )
        except Exception as exc:
            elapsed = round(time.monotonic() - start, 2)
            self._safe_finish_record(
                run_id=run_id,
                status="failed",
                processed=total_processed,
                changed=total_changed,
                elapsed_seconds=elapsed,
                error_message=str(exc)[:1000],
            )
            logger.exception("Error durante la sincronización del archivo RNC de la DGII.")
            raise

    def _safe_finish_record(
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
        if run_id is None:
            return
        try:
            self._repository.record_sync_finish(
                run_id=run_id,
                status=status,
                processed=processed,
                changed=changed,
                elapsed_seconds=elapsed_seconds,
                source=source,
                etag=etag,
                content_hash=content_hash,
                error_message=error_message,
            )
        except Exception:
            logger.exception("No se pudo registrar el resultado de la sincronización.")

    def _invalidate_caches(self) -> None:
        for callback in self._on_catalog_updated:
            try:
                callback()
            except Exception:
                logger.warning("Error invalidando caché tras sincronización.", exc_info=True)


class InitializeCatalogUseCase:
    """Caso de uso ejecutado al arrancar la aplicación."""

    def __init__(
        self,
        repository: ContribuyenteRepository,
        sync_use_case: SyncRncCatalogUseCase,
    ) -> None:
        self._repository = repository
        self._sync_use_case = sync_use_case
        self._initializing = False

    def is_initializing(self) -> bool:
        """Indica si la carga inicial del catálogo sigue en progreso."""
        return self._initializing

    def _run_initial_sync(self) -> None:
        try:
            self._sync_use_case.execute()
        except Exception:
            logger.error(
                "Falló la sincronización inicial en startup; "
                "puedes reintentarla mediante POST /api/sync o CLI."
            )
        finally:
            self._initializing = False

    def execute(self, run_async: bool = True) -> None:
        """Inicializa el esquema, limpia estados huérfanos y carga si la tabla está vacía."""
        self._repository.init_schema()
        cleaned = self._repository.cleanup_stale_sync_runs()
        if cleaned > 0:
            logger.warning(
                "Se marcaron %d sincronizaciones huérfanas previas como interrumpidas.",
                cleaned,
            )
        total = self._repository.count()
        logger.info("Registros actuales en base de datos: %d", total)

        if total == 0:
            self._initializing = True
            if run_async:
                logger.info(
                    "Tabla vacía en startup; iniciando descarga inicial en segundo plano..."
                )
                thread = threading.Thread(
                    target=self._run_initial_sync,
                    name="dgii-initial-sync",
                    daemon=True,
                )
                thread.start()
            else:
                logger.info("Tabla vacía en startup; ejecutando sincronización inicial...")
                self._run_initial_sync()


class GetStatsUseCase:
    """Caso de uso para consultar métricas y estado de actualización del padrón."""

    def __init__(
        self,
        repository: ContribuyenteRepository,
        sync_use_case: SyncRncCatalogUseCase,
        stats_cache: TtlLruCache[str, CatalogStats] | None = None,
    ) -> None:
        self._repository = repository
        self._sync_use_case = sync_use_case
        self._stats_cache = stats_cache

    def execute(self) -> CatalogStats:
        is_syncing = self._sync_use_case.is_running()
        if self._stats_cache is not None:
            hit, cached = self._stats_cache.get_entry("stats")
            if hit and cached is not None:
                return replace(cached, is_syncing=is_syncing)

        stats = self._repository.get_stats(is_syncing=is_syncing)
        if self._stats_cache is not None:
            self._stats_cache.set("stats", stats)
        return stats


class CheckHealthUseCase:
    """Caso de uso para verificar el estado de la persistencia y el catálogo."""

    def __init__(
        self,
        repository: ContribuyenteRepository,
        count_cache: TtlLruCache[str, int] | None = None,
    ) -> None:
        self._repository = repository
        self._count_cache = count_cache

    def execute(self) -> bool:
        return self._repository.is_healthy()

    def has_records(self) -> bool:
        return self._repository.has_records()

    def count_records(self) -> int:
        if self._count_cache is not None:
            hit, cached = self._count_cache.get_entry("count")
            if hit and cached is not None:
                return cached
        total = self._repository.count()
        if self._count_cache is not None:
            self._count_cache.set("count", total)
        return total

    def get_health_snapshot(self) -> tuple[bool, bool, int]:
        """Retorna (healthy, catalog_ready, total_contribuyentes) minimizando escaneos."""
        if not self._repository.is_healthy():
            return False, False, 0

        catalog_ready = self._repository.has_records()
        if self._count_cache is not None:
            hit, cached = self._count_cache.get_entry("count")
            if hit and cached is not None and (cached > 0) == catalog_ready:
                return True, catalog_ready, cached

        total = self._repository.count() if catalog_ready else 0
        if self._count_cache is not None:
            self._count_cache.set("count", total)
        return True, catalog_ready, total
