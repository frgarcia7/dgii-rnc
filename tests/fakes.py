"""Dobles de prueba en memoria compartidos para pruebas unitarias y de API."""

from __future__ import annotations

import threading
import unicodedata
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from app.domain.entities import (
    CatalogStats,
    Contribuyente,
    DgiiFetchResult,
    SearchResult,
    SyncRun,
)


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).upper()


class InMemoryRepo:
    """Repositorio en memoria para pruebas aisladas sin PostgreSQL."""

    def __init__(self, initial: list[Contribuyente] | None = None) -> None:
        self.schema_initialized = False
        self.healthy = True
        self.lock_busy = False
        self._lock = threading.Lock()
        self._runs: list[SyncRun] = []
        self._next_run_id = 1
        self.count_calls = 0
        self.stats_calls = 0
        self.get_by_rnc_calls = 0
        self.analyze_calls = 0
        self.fail_next_error: Exception | None = None
        self.data: dict[str, Contribuyente] = {
            item.rnc: item for item in (initial or [])
        }

    def _maybe_fail(self) -> None:
        if self.fail_next_error is not None:
            exc = self.fail_next_error
            self.fail_next_error = None
            raise exc

    def init_schema(self) -> None:
        self.schema_initialized = True

    def count(self) -> int:
        self._maybe_fail()
        self.count_calls += 1
        return len(self.data)

    def has_records(self) -> bool:
        self._maybe_fail()
        return bool(self.data)

    def get_by_rnc(self, rnc: str) -> Contribuyente | None:
        self._maybe_fail()
        self.get_by_rnc_calls += 1
        return self.data.get(rnc)

    def get_by_rncs(self, rncs: list[str]) -> list[Contribuyente]:
        self._maybe_fail()
        return [self.data[rnc] for rnc in rncs if rnc in self.data]

    def search_by_razon_social(
        self,
        query: str,
        limit: int,
        offset: int,
        estatus: str | None = None,
        regimen: str | None = None,
        actividad: str | None = None,
    ) -> SearchResult:
        self._maybe_fail()
        q = _strip_accents(query)
        act_q = _strip_accents(actividad) if actividad else None

        matches = []
        for item in self.data.values():
            if q not in _strip_accents(item.razon_social):
                continue
            if estatus and item.estatus.upper() != estatus.upper():
                continue
            if regimen and item.regimen.upper() != regimen.upper():
                continue
            if act_q and act_q not in _strip_accents(item.actividad_economica):
                continue
            matches.append(item)

        matches.sort(
            key=lambda item: (
                0 if _strip_accents(item.razon_social).startswith(q) else 1,
                item.razon_social,
            )
        )
        return SearchResult(items=matches[offset : offset + limit], total=len(matches))

    def upsert_batch(self, items: list[Contribuyente]) -> tuple[int, int]:
        changed = 0
        for item in items:
            existing = self.data.get(item.rnc)
            if existing is None or (
                existing.razon_social,
                existing.actividad_economica,
                existing.fecha_inicio_operaciones,
                existing.estatus,
                existing.regimen,
            ) != (
                item.razon_social,
                item.actividad_economica,
                item.fecha_inicio_operaciones,
                item.estatus,
                item.regimen,
            ):
                changed += 1
            self.data[item.rnc] = item
        return len(items), changed

    @contextmanager
    def sync_lock(self) -> Iterator[bool]:
        if self.lock_busy:
            yield False
            return
        acquired = self._lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                self._lock.release()

    def is_sync_locked(self) -> bool:
        return self.lock_busy or self._lock.locked()

    def cleanup_stale_sync_runs(self) -> int:
        if self.is_sync_locked():
            return 0
        cleaned = 0
        for idx, run in enumerate(self._runs):
            if run.status == "running":
                cleaned += 1
                self._runs[idx] = SyncRun(
                    id=run.id,
                    status="interrupted",
                    source=run.source,
                    processed=run.processed,
                    changed=run.changed,
                    elapsed_seconds=run.elapsed_seconds,
                    etag=run.etag,
                    content_hash=run.content_hash,
                    error_message=run.error_message
                    or "Sincronización interrumpida por reinicio del servicio.",
                    started_at=run.started_at,
                    finished_at=datetime.now(timezone.utc),
                )
        return cleaned

    def analyze_catalog(self) -> None:
        self.analyze_calls += 1

    def get_last_successful_sync(self) -> SyncRun | None:
        for run in reversed(self._runs):
            if run.status == "completed":
                return run
        return None

    def get_latest_sync(self) -> SyncRun | None:
        return self._runs[-1] if self._runs else None

    def record_sync_start(self, source: str) -> int | None:
        run_id = self._next_run_id
        self._next_run_id += 1
        self._runs.append(
            SyncRun(
                id=run_id,
                status="running",
                source=source,
                processed=0,
                changed=0,
                elapsed_seconds=0.0,
                started_at=datetime.now(timezone.utc),
            )
        )
        return run_id

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
        if run_id is None:
            return
        for idx, run in enumerate(self._runs):
            if run.id == run_id:
                self._runs[idx] = SyncRun(
                    id=run.id,
                    status=status,
                    source=source or run.source,
                    processed=processed,
                    changed=changed,
                    elapsed_seconds=elapsed_seconds,
                    etag=etag or run.etag,
                    content_hash=content_hash or run.content_hash,
                    error_message=error_message,
                    started_at=run.started_at,
                    finished_at=datetime.now(timezone.utc),
                )
                return

    def get_stats(self, is_syncing: bool) -> CatalogStats:
        self._maybe_fail()
        self.stats_calls += 1
        by_estatus: dict[str, int] = {}
        by_regimen: dict[str, int] = {}
        for item in self.data.values():
            est = item.estatus or "SIN ESTATUS"
            reg = item.regimen or "SIN RÉGIMEN"
            by_estatus[est] = by_estatus.get(est, 0) + 1
            by_regimen[reg] = by_regimen.get(reg, 0) + 1

        return CatalogStats(
            total_contribuyentes=len(self.data),
            is_syncing=is_syncing,
            by_estatus=by_estatus,
            by_regimen=by_regimen,
            last_sync=self.get_latest_sync(),
        )

    def is_healthy(self) -> bool:
        return self.healthy


class InMemorySource:
    """Fuente simulada de lotes DGII."""

    def __init__(
        self,
        batches: list[list[Contribuyente]],
        etag: str | None = '"v1"',
        content_hash: str | None = "hash-v1",
        fail_with: Exception | None = None,
    ) -> None:
        self._batches = batches
        self._etag = etag
        self._content_hash = content_hash
        self._fail_with = fail_with
        self.calls: list[dict[str, object]] = []

    def fetch(
        self,
        batch_size: int,
        known_etag: str | None = None,
        known_hash: str | None = None,
        force: bool = False,
    ) -> DgiiFetchResult:
        self.calls.append(
            {
                "batch_size": batch_size,
                "known_etag": known_etag,
                "known_hash": known_hash,
                "force": force,
            }
        )
        if self._fail_with is not None:
            raise self._fail_with

        if not force and (
            (known_etag and self._etag == known_etag)
            or (known_hash and self._content_hash == known_hash)
        ):
            return DgiiFetchResult(
                not_modified=True,
                source_label="memory://dgii",
                etag=self._etag,
                content_hash=self._content_hash,
            )

        return DgiiFetchResult(
            not_modified=False,
            source_label="memory://dgii",
            etag=self._etag,
            content_hash=self._content_hash,
            batches=iter(self._batches),
        )
