"""Implementación PostgreSQL de ContribuyenteRepository con psycopg2 directo."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from psycopg2.extras import execute_values

from app.domain.entities import CatalogStats, Contribuyente, SearchResult, SyncRun
from app.infrastructure.db.connection import PostgresConnectionManager

logger = logging.getLogger(__name__)

_SYNC_ADVISORY_LOCK_KEY = 420817263

UPSERT_SQL = """
INSERT INTO contribuyentes (
    rnc,
    razon_social,
    actividad_economica,
    fecha_inicio_operaciones,
    estatus,
    regimen,
    updated_at
)
VALUES %s
ON CONFLICT (rnc) DO UPDATE SET
    razon_social = EXCLUDED.razon_social,
    actividad_economica = EXCLUDED.actividad_economica,
    fecha_inicio_operaciones = EXCLUDED.fecha_inicio_operaciones,
    estatus = EXCLUDED.estatus,
    regimen = EXCLUDED.regimen,
    updated_at = NOW()
WHERE (
    contribuyentes.razon_social,
    contribuyentes.actividad_economica,
    contribuyentes.fecha_inicio_operaciones,
    contribuyentes.estatus,
    contribuyentes.regimen
) IS DISTINCT FROM (
    EXCLUDED.razon_social,
    EXCLUDED.actividad_economica,
    EXCLUDED.fecha_inicio_operaciones,
    EXCLUDED.estatus,
    EXCLUDED.regimen
);
"""

_SELECT_COLS = """
    rnc,
    razon_social,
    actividad_economica,
    fecha_inicio_operaciones,
    estatus,
    regimen,
    updated_at
"""


def _escape_like(value: str) -> str:
    """Escapa comodines LIKE/ILIKE para búsquedas literales seguras."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _row_to_entity(row: dict[str, Any]) -> Contribuyente:
    """Mapea una fila RealDictRow a la entidad de dominio Contribuyente."""
    return Contribuyente(
        rnc=row["rnc"],
        razon_social=row["razon_social"],
        actividad_economica=row["actividad_economica"],
        fecha_inicio_operaciones=row["fecha_inicio_operaciones"],
        estatus=row["estatus"],
        regimen=row["regimen"],
        updated_at=row.get("updated_at"),
    )


def _row_to_sync_run(row: dict[str, Any]) -> SyncRun:
    """Mapea una fila de sync_runs a la entidad SyncRun."""
    return SyncRun(
        id=int(row["id"]) if row.get("id") is not None else None,
        status=row["status"],
        source=row["source"],
        processed=int(row["processed"]),
        changed=int(row["changed"]),
        elapsed_seconds=float(row["elapsed_seconds"]),
        etag=row.get("etag"),
        content_hash=row.get("content_hash"),
        error_message=row.get("error_message"),
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
    )


class PostgresContribuyenteRepository:
    """Repositorio PostgreSQL usando psycopg2, pg_trgm, unaccent y execute_values."""

    def __init__(self, conn_manager: PostgresConnectionManager) -> None:
        self._conn_manager = conn_manager

    def init_schema(self) -> None:
        """Crea extensiones, tablas, función immutable_unaccent, migraciones e índices."""
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
                cur.execute("CREATE EXTENSION IF NOT EXISTS unaccent SCHEMA public;")
                cur.execute(
                    """
                    CREATE OR REPLACE FUNCTION immutable_unaccent(text)
                    RETURNS text
                    LANGUAGE sql
                    IMMUTABLE PARALLEL SAFE STRICT
                    AS $$ SELECT public.unaccent('public.unaccent'::regdictionary, $1) $$;
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS contribuyentes (
                        rnc VARCHAR(20) PRIMARY KEY,
                        razon_social TEXT NOT NULL,
                        actividad_economica TEXT NOT NULL DEFAULT '',
                        fecha_inicio_operaciones VARCHAR(50) NOT NULL DEFAULT '',
                        estatus TEXT NOT NULL DEFAULT '',
                        regimen TEXT NOT NULL DEFAULT '',
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
                # Migración idempotente desde versiones previas de la tabla
                cur.execute(
                    """
                    ALTER TABLE contribuyentes
                        ADD COLUMN IF NOT EXISTS actividad_economica TEXT NOT NULL DEFAULT '',
                        ADD COLUMN IF NOT EXISTS
                            fecha_inicio_operaciones VARCHAR(50) NOT NULL DEFAULT '';
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE contribuyentes
                        ALTER COLUMN regimen TYPE TEXT,
                        ALTER COLUMN estatus TYPE TEXT;
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE contribuyentes
                        DROP COLUMN IF EXISTS nombre_comercial,
                        DROP COLUMN IF EXISTS categoria,
                        DROP COLUMN IF EXISTS fecha_constitucion;
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_contribuyentes_razon_social_unaccent_trgm
                    ON contribuyentes USING gin (immutable_unaccent(razon_social) gin_trgm_ops);
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_contribuyentes_estatus
                    ON contribuyentes (estatus);
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_contribuyentes_regimen
                    ON contribuyentes (regimen);
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS sync_runs (
                        id BIGSERIAL PRIMARY KEY,
                        status VARCHAR(30) NOT NULL,
                        source TEXT NOT NULL,
                        processed INTEGER NOT NULL DEFAULT 0,
                        changed INTEGER NOT NULL DEFAULT 0,
                        elapsed_seconds DOUBLE PRECISION NOT NULL DEFAULT 0,
                        etag TEXT,
                        content_hash TEXT,
                        error_message TEXT,
                        started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        finished_at TIMESTAMPTZ
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_sync_runs_status_started
                    ON sync_runs (status, started_at DESC);
                    """
                )

        logger.info(
            "Base de datos inicializada (contribuyentes, sync_runs, unaccent y pg_trgm listos)."
        )

    def count(self) -> int:
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total FROM contribuyentes;")
                row = cur.fetchone()
                return int(row["total"]) if row else 0

    def get_by_rnc(self, rnc: str) -> Contribuyente | None:
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_SELECT_COLS}
                    FROM contribuyentes
                    WHERE rnc = %s;
                    """,
                    (rnc,),
                )
                row = cur.fetchone()
                return _row_to_entity(dict(row)) if row else None

    def get_by_rncs(self, rncs: list[str]) -> list[Contribuyente]:
        if not rncs:
            return []
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT {_SELECT_COLS}
                    FROM contribuyentes
                    WHERE rnc = ANY(%s);
                    """,
                    (rncs,),
                )
                return [_row_to_entity(dict(row)) for row in cur.fetchall()]

    def search_by_razon_social(
        self,
        query: str,
        limit: int,
        offset: int,
        estatus: str | None = None,
        regimen: str | None = None,
        actividad: str | None = None,
    ) -> SearchResult:
        escaped_query = _escape_like(query)
        contains_pattern = f"%{escaped_query}%"
        prefix_pattern = f"{escaped_query}%"

        clauses = ["immutable_unaccent(razon_social) ILIKE immutable_unaccent(%s) ESCAPE '\\'"]
        where_params: list[Any] = [contains_pattern]

        if estatus:
            clauses.append("UPPER(estatus) = %s")
            where_params.append(estatus.upper())
        if regimen:
            clauses.append("UPPER(regimen) = %s")
            where_params.append(regimen.upper())
        if actividad:
            clauses.append(
                "immutable_unaccent(actividad_economica) ILIKE immutable_unaccent(%s) ESCAPE '\\'"
            )
            where_params.append(f"%{_escape_like(actividad)}%")

        where_sql = " AND ".join(clauses)
        order_params: list[Any] = [prefix_pattern, query, limit, offset]

        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT set_config('statement_timeout', %s, true);",
                    (f"{int(self._conn_manager.statement_timeout_ms)}ms",),
                )
                cur.execute(
                    f"""
                    SELECT
                        {_SELECT_COLS},
                        COUNT(*) OVER() AS total_count
                    FROM contribuyentes
                    WHERE {where_sql}
                    ORDER BY
                        CASE
                            WHEN immutable_unaccent(razon_social)
                                ILIKE immutable_unaccent(%s) ESCAPE '\\'
                            THEN 0 ELSE 1
                        END ASC,
                        similarity(immutable_unaccent(razon_social), immutable_unaccent(%s)) DESC,
                        razon_social ASC
                    LIMIT %s OFFSET %s;
                    """,
                    tuple(where_params + order_params),
                )
                rows = cur.fetchall()

                if rows:
                    total = int(rows[0]["total_count"])
                elif offset > 0:
                    cur.execute(
                        f"SELECT COUNT(*) AS total FROM contribuyentes WHERE {where_sql};",
                        tuple(where_params),
                    )
                    count_row = cur.fetchone()
                    total = int(count_row["total"]) if count_row else 0
                else:
                    total = 0

                items = [_row_to_entity(dict(row)) for row in rows]
                return SearchResult(items=items, total=total)

    def upsert_batch(self, items: list[Contribuyente]) -> tuple[int, int]:
        if not items:
            return 0, 0

        rows = [
            (
                item.rnc,
                item.razon_social,
                item.actividad_economica,
                item.fecha_inicio_operaciones,
                item.estatus,
                item.regimen,
            )
            for item in items
        ]
        page_size = 5_000
        changed = 0
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                for start in range(0, len(rows), page_size):
                    chunk = rows[start : start + page_size]
                    execute_values(
                        cur,
                        UPSERT_SQL,
                        chunk,
                        template="(%s, %s, %s, %s, %s, %s, NOW())",
                        page_size=page_size,
                    )
                    changed += max(cur.rowcount, 0)
        return len(rows), changed

    @contextmanager
    def sync_lock(self) -> Iterator[bool]:
        """Bloqueo distribuido entre procesos vía PostgreSQL advisory lock."""
        with self._conn_manager.connection() as conn:
            acquired = False
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_try_advisory_lock(%s) AS locked;",
                        (_SYNC_ADVISORY_LOCK_KEY,),
                    )
                    row = cur.fetchone()
                    acquired = bool(row and row["locked"])
                yield acquired
            finally:
                if acquired and not conn.closed:
                    try:
                        with conn.cursor() as cur:
                            cur.execute(
                                "SELECT pg_advisory_unlock(%s);",
                                (_SYNC_ADVISORY_LOCK_KEY,),
                            )
                        conn.commit()
                    except Exception:
                        logger.warning(
                            "No se pudo liberar el advisory lock explícitamente; "
                            "se liberará al cerrar la sesión."
                        )

    def get_last_successful_sync(self) -> SyncRun | None:
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id, status, source, processed, changed, elapsed_seconds,
                        etag, content_hash, error_message, started_at, finished_at
                    FROM sync_runs
                    WHERE status = 'completed'
                    ORDER BY started_at DESC
                    LIMIT 1;
                    """
                )
                row = cur.fetchone()
                return _row_to_sync_run(dict(row)) if row else None

    def get_latest_sync(self) -> SyncRun | None:
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id, status, source, processed, changed, elapsed_seconds,
                        etag, content_hash, error_message, started_at, finished_at
                    FROM sync_runs
                    ORDER BY started_at DESC
                    LIMIT 1;
                    """
                )
                row = cur.fetchone()
                return _row_to_sync_run(dict(row)) if row else None

    def record_sync_start(self, source: str) -> int | None:
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO sync_runs (status, source)
                    VALUES ('running', %s)
                    RETURNING id;
                    """,
                    (source,),
                )
                row = cur.fetchone()
                return int(row["id"]) if row else None

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
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE sync_runs
                    SET
                        status = %s,
                        source = COALESCE(%s, source),
                        processed = %s,
                        changed = %s,
                        elapsed_seconds = %s,
                        etag = COALESCE(%s, etag),
                        content_hash = COALESCE(%s, content_hash),
                        error_message = %s,
                        finished_at = NOW()
                    WHERE id = %s;
                    """,
                    (
                        status,
                        source,
                        processed,
                        changed,
                        elapsed_seconds,
                        etag,
                        content_hash,
                        error_message,
                        run_id,
                    ),
                )

    def get_stats(self, is_syncing: bool) -> CatalogStats:
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total FROM contribuyentes;")
                total_row = cur.fetchone()
                total = int(total_row["total"]) if total_row else 0

                cur.execute(
                    """
                    SELECT COALESCE(NULLIF(estatus, ''), 'SIN ESTATUS') AS label, COUNT(*) AS qty
                    FROM contribuyentes
                    GROUP BY 1
                    ORDER BY qty DESC;
                    """
                )
                by_estatus = {row["label"]: int(row["qty"]) for row in cur.fetchall()}

                cur.execute(
                    """
                    SELECT COALESCE(NULLIF(regimen, ''), 'SIN RÉGIMEN') AS label, COUNT(*) AS qty
                    FROM contribuyentes
                    GROUP BY 1
                    ORDER BY qty DESC;
                    """
                )
                by_regimen = {row["label"]: int(row["qty"]) for row in cur.fetchall()}

                cur.execute(
                    """
                    SELECT
                        id, status, source, processed, changed, elapsed_seconds,
                        etag, content_hash, error_message, started_at, finished_at
                    FROM sync_runs
                    ORDER BY started_at DESC
                    LIMIT 1;
                    """
                )
                last_row = cur.fetchone()
                last_sync = _row_to_sync_run(dict(last_row)) if last_row else None

        return CatalogStats(
            total_contribuyentes=total,
            is_syncing=is_syncing,
            by_estatus=by_estatus,
            by_regimen=by_regimen,
            last_sync=last_sync,
        )

    def is_healthy(self) -> bool:
        try:
            with self._conn_manager.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1 AS ok;")
                    row = cur.fetchone()
                    return bool(row and row.get("ok") == 1)
        except Exception:
            logger.exception("Fallo en healthcheck de PostgreSQL.")
            return False
