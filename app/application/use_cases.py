"""Casos de uso de la aplicación (orquestan dominio sin conocer frameworks ni SQL)."""

from __future__ import annotations

import logging
import threading
import time

from app.domain.entities import Contribuyente, SyncReport, normalize_rnc
from app.domain.exceptions import ContribuyenteNotFoundError, InvalidInputError
from app.domain.ports import ContribuyenteRepository, DgiiSourcePort

logger = logging.getLogger(__name__)


class GetContribuyenteByRncUseCase:
    """Caso de uso para consultar un contribuyente exacto por RNC o Cédula."""

    def __init__(self, repository: ContribuyenteRepository) -> None:
        self._repository = repository

    def execute(self, raw_rnc: str) -> Contribuyente:
        normalized = normalize_rnc(raw_rnc)
        if not normalized:
            raise InvalidInputError("El RNC proporcionado no es válido.")

        contribuyente = self._repository.get_by_rnc(normalized)
        if contribuyente is None:
            raise ContribuyenteNotFoundError(normalized)

        return contribuyente


class SearchContribuyentesUseCase:
    """Caso de uso para buscar contribuyentes por coincidencia parcial en razón social."""

    def __init__(self, repository: ContribuyenteRepository) -> None:
        self._repository = repository

    def execute(
        self, raw_query: str, limit: int = 50, offset: int = 0
    ) -> list[Contribuyente]:
        cleaned = raw_query.strip()
        if not cleaned:
            raise InvalidInputError("El parámetro 'query' no puede estar vacío.")

        return self._repository.search_by_razon_social(
            query=cleaned, limit=limit, offset=offset
        )


class SyncRncCatalogUseCase:
    """Caso de uso para descargar e impactar por lotes el padrón RNC de la DGII."""

    def __init__(
        self,
        repository: ContribuyenteRepository,
        source: DgiiSourcePort,
        batch_size: int = 10_000,
    ) -> None:
        self._repository = repository
        self._source = source
        self._batch_size = batch_size
        self._lock = threading.Lock()

    def is_running(self) -> bool:
        """Indica si existe una sincronización en progreso."""
        return self._lock.locked()

    def execute(self) -> SyncReport:
        """Ejecuta la sincronización completa evitando ejecuciones concurrentes."""
        if not self._lock.acquire(blocking=False):
            logger.warning("Se omitió la sincronización porque ya hay una en curso.")
            return SyncReport(status="already_running", processed=0)

        start = time.monotonic()
        total_processed = 0

        try:
            for batch in self._source.stream_batches(self._batch_size):
                total_processed += self._repository.upsert_batch(batch)
                if total_processed % 100_000 == 0:
                    logger.info("Registros sincronizados: %d...", total_processed)

            elapsed = round(time.monotonic() - start, 2)
            logger.info(
                "Sincronización completada: %d registros procesados en %.2f s.",
                total_processed,
                elapsed,
            )
            return SyncReport(
                status="completed",
                processed=total_processed,
                elapsed_seconds=elapsed,
            )
        except Exception:
            logger.exception("Error durante la sincronización del archivo RNC de la DGII.")
            raise
        finally:
            self._lock.release()


class InitializeCatalogUseCase:
    """Caso de uso ejecutado al arrancar la aplicación."""

    def __init__(
        self,
        repository: ContribuyenteRepository,
        sync_use_case: SyncRncCatalogUseCase,
    ) -> None:
        self._repository = repository
        self._sync_use_case = sync_use_case

    def execute(self) -> None:
        """Inicializa el esquema y ejecuta la carga inicial si la tabla está vacía."""
        self._repository.init_schema()
        total = self._repository.count()
        logger.info("Registros actuales en base de datos: %d", total)

        if total == 0:
            logger.info("Tabla vacía en startup; ejecutando sincronización inicial...")
            try:
                self._sync_use_case.execute()
            except Exception:
                logger.error(
                    "Falló la sincronización inicial en startup; "
                    "puedes reintentarla manualmente mediante POST /api/sync."
                )


class CheckHealthUseCase:
    """Caso de uso para verificar el estado de la persistencia."""

    def __init__(self, repository: ContribuyenteRepository) -> None:
        self._repository = repository

    def execute(self) -> bool:
        return self._repository.is_healthy()

