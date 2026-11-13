"""Dobles de prueba en memoria compartidos para pruebas unitarias y de API."""

from __future__ import annotations

import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
from difflib import SequenceMatcher
from typing import Iterator

from app.domain.entities import (
    CatalogStats,
    Contribuyente,
    ContribuyenteCambio,
    DgiiFetchResult,
    SearchResult,
    SyncRun,
)


def _strip_accents(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).upper()


def _word_similarity(query: str, target: str) -> float:
    """Similitud aproximada por palabra/frase equivalente a pg_trgm word_similarity."""
    q = _strip_accents(query)
    t = _strip_accents(target)
    if not q or not t:
        return 0.0
    if q in t:
        return 1.0
    words = t.split()
    q_words = q.split()
    best = SequenceMatcher(None, q, t).ratio()
    window = max(1, len(q_words))
    for i in range(max(1, len(words) - window + 1)):
        chunk = " ".join(words[i : i + window])
        best = max(best, SequenceMatcher(None, q, chunk).ratio())
    return best


class InMemoryRepo:
    """Repositorio en memoria para pruebas aisladas sin PostgreSQL."""

    def __init__(self, initial: list[Contribuyente] | None = None) -> None:
        self.schema_initialized = False
        self.healthy = True
        self.lock_busy = False
        self._lock = threading.Lock()
        self._runs: list[SyncRun] = []
        self._history: list[ContribuyenteCambio] = []
        self._seen_rncs: set[str] = set()
        self._next_run_id = 1
        self._next_history_id = 1
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

    def _matches_filters(
        self,
        item: Contribuyente,
        estatus: str | None,
        regimen: str | None,
        actividad: str | None,
    ) -> bool:
        if estatus and item.estatus.upper() != estatus.upper():
            return False
        if regimen and item.regimen.upper() != regimen.upper():
            return False
        if actividad:
            act_norm = _strip_accents(item.actividad_economica)
            act_tokens = [tok for tok in _strip_accents(actividad).split() if tok]
            if not all(tok in act_norm for tok in act_tokens):
                return False
        return True

    def search_by_razon_social(
        self,
        query: str | None,
        limit: int,
        offset: int,
        estatus: str | None = None,
        regimen: str | None = None,
        actividad: str | None = None,
    ) -> SearchResult:
        self._maybe_fail()
        filtered = [
            item
            for item in self.data.values()
            if self._matches_filters(item, estatus, regimen, actividad)
        ]

        if query is None:
            filtered.sort(key=lambda item: item.razon_social)
            return SearchResult(
                items=filtered[offset : offset + limit], total=len(filtered)
            )

        compact_digits = query.replace("-", "").replace(" ", "")
        is_numeric = compact_digits.isascii() and compact_digits.isdigit()
        q = _strip_accents(query)

        if is_numeric:
            matches = [
                item
                for item in filtered
                if item.rnc.startswith(compact_digits)
                or (len(compact_digits) >= 3 and q in _strip_accents(item.razon_social))
            ]
            matches.sort(
                key=lambda item: (
                    (
                        0
                        if item.rnc == compact_digits
                        else 1
                        if item.rnc.startswith(compact_digits)
                        else 2
                    ),
                    item.razon_social,
                )
            )
            return SearchResult(items=matches[offset : offset + limit], total=len(matches))

        tokens = [tok for tok in q.split() if tok]
        if len(query) < 3:
            matches = [
                item for item in filtered if _strip_accents(item.razon_social).startswith(q)
            ]
        elif len(tokens) > 1 and any(len(tok) >= 3 for tok in tokens):
            matches = [
                item
                for item in filtered
                if all(tok in _strip_accents(item.razon_social) for tok in tokens)
            ]
        else:
            matches = [item for item in filtered if q in _strip_accents(item.razon_social)]

        if not matches and offset == 0 and len(query) >= 4:
            scored = [
                (_word_similarity(query, item.razon_social), item) for item in filtered
            ]
            fuzzy = [pair for pair in scored if pair[0] >= 0.65]
            fuzzy.sort(key=lambda pair: (-pair[0], pair[1].razon_social))
            matches = [item for _, item in fuzzy]
            return SearchResult(items=matches[:limit], total=len(matches))

        matches.sort(
            key=lambda item: (
                0
                if _strip_accents(item.razon_social).startswith(q)
                else 1
                if q in _strip_accents(item.razon_social)
                else 2,
                item.razon_social,
            )
        )
        return SearchResult(items=matches[offset : offset + limit], total=len(matches))

    def get_history_by_rnc(self, rnc: str, limit: int = 20) -> list[ContribuyenteCambio]:
        self._maybe_fail()
        matches = [ev for ev in self._history if ev.rnc == rnc]
        matches.sort(key=lambda ev: ev.id or 0, reverse=True)
        return matches[:limit]

    def begin_sync_tracking(self) -> None:
        self._seen_rncs.clear()

    def upsert_batch(
        self,
        items: list[Contribuyente],
        sync_run_id: int | None = None,
        record_history: bool = False,
        track_seen: bool = False,
    ) -> tuple[int, int]:
        changed = 0
        now = datetime.now(timezone.utc)
        for item in items:
            if track_seen:
                self._seen_rncs.add(item.rnc)

            existing = self.data.get(item.rnc)
            if existing is None:
                changed += 1
                if record_history:
                    self._append_history(
                        rnc=item.rnc,
                        sync_run_id=sync_run_id,
                        cambio_tipo="ALTA",
                        razon_social_nueva=item.razon_social,
                        actividad_nueva=item.actividad_economica,
                        estatus_nuevo=item.estatus,
                        regimen_nuevo=item.regimen,
                        detected_at=now,
                    )
                self.data[item.rnc] = replace(item, en_padron=True, updated_at=now)
                continue

            reentered = not existing.en_padron
            data_changed = (
                existing.razon_social,
                existing.actividad_economica,
                existing.estatus,
                existing.regimen,
            ) != (
                item.razon_social,
                item.actividad_economica,
                item.estatus,
                item.regimen,
            )
            fecha_changed = existing.fecha_inicio_operaciones != item.fecha_inicio_operaciones

            if reentered or data_changed:
                if record_history:
                    tipo = "REINGRESO" if reentered else "ACTUALIZACION"
                    self._append_history(
                        rnc=item.rnc,
                        sync_run_id=sync_run_id,
                        cambio_tipo=tipo,
                        razon_social_anterior=(
                            existing.razon_social
                            if reentered or existing.razon_social != item.razon_social
                            else None
                        ),
                        razon_social_nueva=(
                            item.razon_social
                            if reentered or existing.razon_social != item.razon_social
                            else None
                        ),
                        actividad_anterior=(
                            existing.actividad_economica
                            if reentered
                            or existing.actividad_economica != item.actividad_economica
                            else None
                        ),
                        actividad_nueva=(
                            item.actividad_economica
                            if reentered
                            or existing.actividad_economica != item.actividad_economica
                            else None
                        ),
                        estatus_anterior=(
                            existing.estatus
                            if reentered or existing.estatus != item.estatus
                            else None
                        ),
                        estatus_nuevo=(
                            item.estatus
                            if reentered or existing.estatus != item.estatus
                            else None
                        ),
                        regimen_anterior=(
                            existing.regimen
                            if reentered or existing.regimen != item.regimen
                            else None
                        ),
                        regimen_nuevo=(
                            item.regimen
                            if reentered or existing.regimen != item.regimen
                            else None
                        ),
                        detected_at=now,
                    )
                changed += 1
                self.data[item.rnc] = replace(item, en_padron=True, updated_at=now)
            elif fecha_changed:
                changed += 1
                self.data[item.rnc] = replace(item, en_padron=True, updated_at=now)

        return len(items), changed

    def finalize_sync_tracking(
        self,
        sync_run_id: int | None,
        processed_count: int,
        record_history: bool = False,
    ) -> int:
        prev_active = sum(1 for item in self.data.values() if item.en_padron)
        if prev_active > 1000 and processed_count < prev_active * 0.5:
            self._seen_rncs.clear()
            return 0

        now = datetime.now(timezone.utc)
        bajas = 0
        for rnc, existing in list(self.data.items()):
            if existing.en_padron and rnc not in self._seen_rncs:
                bajas += 1
                self.data[rnc] = replace(existing, en_padron=False, updated_at=now)
                if record_history:
                    self._append_history(
                        rnc=rnc,
                        sync_run_id=sync_run_id,
                        cambio_tipo="BAJA_PADRON",
                        razon_social_anterior=existing.razon_social,
                        actividad_anterior=existing.actividad_economica,
                        estatus_anterior=existing.estatus,
                        regimen_anterior=existing.regimen,
                        detected_at=now,
                    )
        self._seen_rncs.clear()
        return bajas

    def _append_history(
        self,
        rnc: str,
        sync_run_id: int | None,
        cambio_tipo: str,
        razon_social_anterior: str | None = None,
        razon_social_nueva: str | None = None,
        actividad_anterior: str | None = None,
        actividad_nueva: str | None = None,
        estatus_anterior: str | None = None,
        estatus_nuevo: str | None = None,
        regimen_anterior: str | None = None,
        regimen_nuevo: str | None = None,
        detected_at: datetime | None = None,
    ) -> None:
        hid = self._next_history_id
        self._next_history_id += 1
        self._history.append(
            ContribuyenteCambio(
                id=hid,
                rnc=rnc,
                sync_run_id=sync_run_id,
                cambio_tipo=cambio_tipo,
                razon_social_anterior=razon_social_anterior,
                razon_social_nueva=razon_social_nueva,
                actividad_anterior=actividad_anterior,
                actividad_nueva=actividad_nueva,
                estatus_anterior=estatus_anterior,
                estatus_nuevo=estatus_nuevo,
                regimen_anterior=regimen_anterior,
                regimen_nuevo=regimen_nuevo,
                detected_at=detected_at or datetime.now(timezone.utc),
            )
        )

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
