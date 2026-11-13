"""Implementación PostgreSQL de ContribuyenteRepository con psycopg2 directo."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from psycopg2.extras import execute_values

from app.domain.entities import (
    CatalogStats,
    Contribuyente,
    ContribuyenteCambio,
    SearchResult,
    SyncRun,
)
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
    en_padron,
    updated_at
)
VALUES %s
ON CONFLICT (rnc) DO UPDATE SET
    razon_social = EXCLUDED.razon_social,
    actividad_economica = EXCLUDED.actividad_economica,
    fecha_inicio_operaciones = EXCLUDED.fecha_inicio_operaciones,
    estatus = EXCLUDED.estatus,
    regimen = EXCLUDED.regimen,
    en_padron = TRUE,
    updated_at = NOW()
WHERE (
    contribuyentes.razon_social,
    contribuyentes.actividad_economica,
    contribuyentes.fecha_inicio_operaciones,
    contribuyentes.estatus,
    contribuyentes.regimen,
    contribuyentes.en_padron
) IS DISTINCT FROM (
    EXCLUDED.razon_social,
    EXCLUDED.actividad_economica,
    EXCLUDED.fecha_inicio_operaciones,
    EXCLUDED.estatus,
    EXCLUDED.regimen,
    TRUE
);
"""

_SELECT_COLS = """
    rnc,
    razon_social,
    actividad_economica,
    fecha_inicio_operaciones,
    estatus,
    regimen,
    en_padron,
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
        en_padron=bool(row.get("en_padron", True)),
    )


def _row_to_cambio(row: dict[str, Any]) -> ContribuyenteCambio:
    """Mapea una fila de contribuyente_historial a la entidad ContribuyenteCambio."""
    return ContribuyenteCambio(
        id=int(row["id"]) if row.get("id") is not None else None,
        rnc=row["rnc"],
        sync_run_id=(
            int(row["sync_run_id"]) if row.get("sync_run_id") is not None else None
        ),
        cambio_tipo=row["cambio_tipo"],
        razon_social_anterior=row.get("razon_social_anterior"),
        razon_social_nueva=row.get("razon_social_nueva"),
        actividad_anterior=row.get("actividad_anterior"),
        actividad_nueva=row.get("actividad_nueva"),
        estatus_anterior=row.get("estatus_anterior"),
        estatus_nuevo=row.get("estatus_nuevo"),
        regimen_anterior=row.get("regimen_anterior"),
        regimen_nuevo=row.get("regimen_nuevo"),
        detected_at=row.get("detected_at"),
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
                        en_padron BOOLEAN NOT NULL DEFAULT TRUE,
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
                            fecha_inicio_operaciones VARCHAR(50) NOT NULL DEFAULT '',
                        ADD COLUMN IF NOT EXISTS en_padron BOOLEAN NOT NULL DEFAULT TRUE;
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
                    CREATE INDEX IF NOT EXISTS idx_contribuyentes_razon_social_prefix
                    ON contribuyentes (LOWER(immutable_unaccent(razon_social)) text_pattern_ops);
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_contribuyentes_actividad_unaccent_trgm
                    ON contribuyentes
                    USING gin (immutable_unaccent(actividad_economica) gin_trgm_ops);
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
                    CREATE INDEX IF NOT EXISTS idx_contribuyentes_estatus_upper
                    ON contribuyentes (UPPER(estatus));
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
                    CREATE INDEX IF NOT EXISTS idx_contribuyentes_regimen_upper
                    ON contribuyentes (UPPER(regimen));
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_contribuyentes_razon_social_fts
                    ON contribuyentes
                    USING gin (to_tsvector('spanish'::regconfig, immutable_unaccent(razon_social)));
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_contribuyentes_rnc_prefix
                    ON contribuyentes (rnc varchar_pattern_ops);
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_contribuyentes_fuera_padron
                    ON contribuyentes (rnc)
                    WHERE NOT en_padron;
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
                cur.execute(
                    """
                    CREATE UNLOGGED TABLE IF NOT EXISTS sync_seen_rncs (
                        rnc VARCHAR(20) PRIMARY KEY
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS contribuyente_historial (
                        id BIGSERIAL PRIMARY KEY,
                        rnc VARCHAR(20) NOT NULL,
                        sync_run_id BIGINT REFERENCES sync_runs(id) ON DELETE SET NULL,
                        cambio_tipo VARCHAR(30) NOT NULL,
                        razon_social_anterior TEXT,
                        razon_social_nueva TEXT,
                        actividad_anterior TEXT,
                        actividad_nueva TEXT,
                        estatus_anterior TEXT,
                        estatus_nuevo TEXT,
                        regimen_anterior TEXT,
                        regimen_nuevo TEXT,
                        detected_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    );
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_historial_rnc_detected
                    ON contribuyente_historial (rnc, detected_at DESC, id DESC);
                    """
                )

        logger.info(
            "Base de datos inicializada "
            "(contribuyentes, historial, sync_runs, FTS español, unaccent y pg_trgm listos)."
        )

    def count(self) -> int:
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total FROM contribuyentes;")
                row = cur.fetchone()
                return int(row["total"]) if row else 0

    def has_records(self) -> bool:
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT EXISTS (SELECT 1 FROM contribuyentes) AS has_records;")
                row = cur.fetchone()
                return bool(row and row["has_records"])

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
        query: str | None,
        limit: int,
        offset: int,
        estatus: str | None = None,
        regimen: str | None = None,
        actividad: str | None = None,
    ) -> SearchResult:
        filter_clauses: list[str] = []
        filter_params: list[Any] = []
        if estatus:
            filter_clauses.append("UPPER(estatus) = %s")
            filter_params.append(estatus.upper())
        if regimen:
            filter_clauses.append("UPPER(regimen) = %s")
            filter_params.append(regimen.upper())
        if actividad:
            act_tokens = [tok for tok in actividad.split() if tok]
            if len(act_tokens) > 1 and any(len(tok) >= 3 for tok in act_tokens):
                for tok in act_tokens:
                    filter_clauses.append(
                        "immutable_unaccent(actividad_economica) "
                        "ILIKE immutable_unaccent(%s) ESCAPE '\\'"
                    )
                    filter_params.append(f"%{_escape_like(tok)}%")
            else:
                filter_clauses.append(
                    "immutable_unaccent(actividad_economica) "
                    "ILIKE immutable_unaccent(%s) ESCAPE '\\'"
                )
                filter_params.append(f"%{_escape_like(actividad)}%")

        is_numeric_query = False
        compact_digits = ""
        if query is not None:
            compact_digits = query.replace("-", "").replace(" ", "")
            is_numeric_query = compact_digits.isascii() and compact_digits.isdigit()

        if query is None:
            clauses = list(filter_clauses)
            where_params = list(filter_params)
            order_sql = "razon_social ASC"
            order_params: list[Any] = []
        elif is_numeric_query:
            prefix_rnc = f"{_escape_like(compact_digits)}%"
            if len(compact_digits) < 3:
                clauses = ["rnc LIKE %s ESCAPE '\\'", *filter_clauses]
                where_params = [prefix_rnc, *filter_params]
            else:
                clauses = [
                    "(rnc LIKE %s ESCAPE '\\' OR "
                    "immutable_unaccent(razon_social) ILIKE immutable_unaccent(%s) ESCAPE '\\')",
                    *filter_clauses,
                ]
                where_params = [prefix_rnc, f"%{_escape_like(query)}%", *filter_params]
            order_sql = (
                "CASE WHEN rnc = %s THEN 0 WHEN rnc LIKE %s ESCAPE '\\' THEN 1 ELSE 2 END ASC, "
                "razon_social ASC"
            )
            order_params = [compact_digits, prefix_rnc]
        elif len(query) < 3:
            prefix_pattern = f"{_escape_like(query)}%"
            clauses = [
                "LOWER(immutable_unaccent(razon_social)) "
                "LIKE LOWER(immutable_unaccent(%s)) ESCAPE '\\'",
                *filter_clauses,
            ]
            where_params = [prefix_pattern, *filter_params]
            order_sql = (
                "similarity(immutable_unaccent(razon_social), immutable_unaccent(%s)) DESC, "
                "razon_social ASC"
            )
            order_params = [query]
        else:
            escaped_query = _escape_like(query)
            prefix_pattern = f"{escaped_query}%"
            contains_pattern = f"%{escaped_query}%"
            tokens = [tok for tok in query.split() if tok]
            if len(tokens) > 1 and any(len(tok) >= 3 for tok in tokens):
                token_exprs = [
                    "immutable_unaccent(razon_social) ILIKE immutable_unaccent(%s) ESCAPE '\\'"
                    for _ in tokens
                ]
                token_sql = " AND ".join(token_exprs)
                token_params: list[Any] = [f"%{_escape_like(tok)}%" for tok in tokens]
            else:
                token_sql = (
                    "immutable_unaccent(razon_social) ILIKE immutable_unaccent(%s) ESCAPE '\\'"
                )
                token_params = [contains_pattern]

            clauses = [
                f"(({token_sql}) OR "
                "to_tsvector('spanish'::regconfig, immutable_unaccent(razon_social)) "
                "@@ plainto_tsquery('spanish'::regconfig, immutable_unaccent(%s)))",
                *filter_clauses,
            ]
            where_params = [*token_params, query, *filter_params]
            order_sql = (
                "CASE "
                "WHEN immutable_unaccent(razon_social) "
                "ILIKE immutable_unaccent(%s) ESCAPE '\\' THEN 0 "
                "WHEN immutable_unaccent(razon_social) "
                "ILIKE immutable_unaccent(%s) ESCAPE '\\' THEN 1 "
                "ELSE 2 END ASC, "
                "similarity(immutable_unaccent(razon_social), immutable_unaccent(%s)) DESC, "
                "razon_social ASC"
            )
            order_params = [prefix_pattern, contains_pattern, query]

        where_sql = " AND ".join(clauses) if clauses else "TRUE"

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
                    ORDER BY {order_sql}
                    LIMIT %s OFFSET %s;
                    """,
                    tuple([*where_params, *order_params, limit, offset]),
                )
                rows = cur.fetchall()

                if (
                    not rows
                    and offset == 0
                    and query is not None
                    and not is_numeric_query
                    and len(query) >= 4
                ):
                    fuzzy_clauses = [
                        "immutable_unaccent(razon_social) %%> immutable_unaccent(%s)",
                        *filter_clauses,
                    ]
                    fuzzy_where = " AND ".join(fuzzy_clauses)
                    fuzzy_params: list[Any] = [query, *filter_params, query, limit, offset]
                    cur.execute(
                        "SELECT set_config('pg_trgm.word_similarity_threshold', '0.5', true);"
                    )
                    cur.execute(
                        f"""
                        SELECT
                            {_SELECT_COLS},
                            COUNT(*) OVER() AS total_count
                        FROM contribuyentes
                        WHERE {fuzzy_where}
                        ORDER BY
                            word_similarity(
                                immutable_unaccent(%s),
                                immutable_unaccent(razon_social)
                            ) DESC,
                            razon_social ASC
                        LIMIT %s OFFSET %s;
                        """,
                        tuple(fuzzy_params),
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

    def get_history_by_rnc(self, rnc: str, limit: int = 20) -> list[ContribuyenteCambio]:
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        id,
                        rnc,
                        sync_run_id,
                        cambio_tipo,
                        razon_social_anterior,
                        razon_social_nueva,
                        actividad_anterior,
                        actividad_nueva,
                        estatus_anterior,
                        estatus_nuevo,
                        regimen_anterior,
                        regimen_nuevo,
                        detected_at
                    FROM contribuyente_historial
                    WHERE rnc = %s
                    ORDER BY detected_at DESC, id DESC
                    LIMIT %s;
                    """,
                    (rnc, limit),
                )
                return [_row_to_cambio(dict(row)) for row in cur.fetchall()]

    def begin_sync_tracking(self) -> None:
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("TRUNCATE TABLE sync_seen_rncs;")

    def upsert_batch(
        self,
        items: list[Contribuyente],
        sync_run_id: int | None = None,
        record_history: bool = False,
        track_seen: bool = False,
    ) -> tuple[int, int]:
        if not items:
            return 0, 0

        deduped: dict[str, Contribuyente] = {item.rnc: item for item in items}
        rows = [
            (
                item.rnc,
                item.razon_social,
                item.actividad_economica,
                item.fecha_inicio_operaciones,
                item.estatus,
                item.regimen,
            )
            for item in deduped.values()
        ]
        page_size = 5_000
        changed = 0

        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                if not track_seen and not record_history:
                    for start in range(0, len(rows), page_size):
                        chunk = rows[start : start + page_size]
                        execute_values(
                            cur,
                            UPSERT_SQL,
                            chunk,
                            template="(%s, %s, %s, %s, %s, %s, TRUE, NOW())",
                            page_size=page_size,
                        )
                        changed += max(cur.rowcount, 0)
                    return len(rows), changed

                cur.execute(
                    """
                    CREATE TEMP TABLE IF NOT EXISTS _batch_in (
                        rnc VARCHAR(20) PRIMARY KEY,
                        razon_social TEXT NOT NULL,
                        actividad_economica TEXT NOT NULL,
                        fecha_inicio_operaciones VARCHAR(50) NOT NULL,
                        estatus TEXT NOT NULL,
                        regimen TEXT NOT NULL
                    ) ON COMMIT DROP;
                    """
                )
                cur.execute("TRUNCATE TABLE _batch_in;")
                execute_values(
                    cur,
                    """
                    INSERT INTO _batch_in (
                        rnc,
                        razon_social,
                        actividad_economica,
                        fecha_inicio_operaciones,
                        estatus,
                        regimen
                    ) VALUES %s
                    ON CONFLICT (rnc) DO NOTHING;
                    """,
                    rows,
                    page_size=page_size,
                )

                if track_seen:
                    cur.execute(
                        """
                        INSERT INTO sync_seen_rncs (rnc)
                        SELECT rnc FROM _batch_in
                        ON CONFLICT (rnc) DO NOTHING;
                        """
                    )

                if record_history:
                    cur.execute(
                        """
                        INSERT INTO contribuyente_historial (
                            rnc,
                            sync_run_id,
                            cambio_tipo,
                            razon_social_anterior,
                            razon_social_nueva,
                            actividad_anterior,
                            actividad_nueva,
                            estatus_anterior,
                            estatus_nuevo,
                            regimen_anterior,
                            regimen_nuevo
                        )
                        SELECT
                            b.rnc,
                            %s,
                            CASE
                                WHEN c.rnc IS NULL THEN 'ALTA'
                                WHEN NOT c.en_padron THEN 'REINGRESO'
                                ELSE 'ACTUALIZACION'
                            END,
                            CASE
                                WHEN c.rnc IS NULL THEN NULL
                                WHEN NOT c.en_padron
                                     OR c.razon_social IS DISTINCT FROM b.razon_social
                                    THEN c.razon_social
                                ELSE NULL
                            END,
                            CASE
                                WHEN c.rnc IS NULL OR NOT c.en_padron
                                     OR c.razon_social IS DISTINCT FROM b.razon_social
                                    THEN b.razon_social
                                ELSE NULL
                            END,
                            CASE
                                WHEN c.rnc IS NULL THEN NULL
                                WHEN NOT c.en_padron
                                     OR c.actividad_economica IS DISTINCT FROM b.actividad_economica
                                    THEN c.actividad_economica
                                ELSE NULL
                            END,
                            CASE
                                WHEN c.rnc IS NULL OR NOT c.en_padron
                                     OR c.actividad_economica IS DISTINCT FROM b.actividad_economica
                                    THEN b.actividad_economica
                                ELSE NULL
                            END,
                            CASE
                                WHEN c.rnc IS NULL THEN NULL
                                WHEN NOT c.en_padron OR c.estatus IS DISTINCT FROM b.estatus
                                    THEN c.estatus
                                ELSE NULL
                            END,
                            CASE
                                WHEN c.rnc IS NULL OR NOT c.en_padron
                                     OR c.estatus IS DISTINCT FROM b.estatus
                                    THEN b.estatus
                                ELSE NULL
                            END,
                            CASE
                                WHEN c.rnc IS NULL THEN NULL
                                WHEN NOT c.en_padron OR c.regimen IS DISTINCT FROM b.regimen
                                    THEN c.regimen
                                ELSE NULL
                            END,
                            CASE
                                WHEN c.rnc IS NULL OR NOT c.en_padron
                                     OR c.regimen IS DISTINCT FROM b.regimen
                                    THEN b.regimen
                                ELSE NULL
                            END
                        FROM _batch_in b
                        LEFT JOIN contribuyentes c ON c.rnc = b.rnc
                        WHERE c.rnc IS NULL
                           OR NOT c.en_padron
                           OR (c.razon_social, c.actividad_economica, c.estatus, c.regimen)
                              IS DISTINCT FROM
                              (b.razon_social, b.actividad_economica, b.estatus, b.regimen);
                        """,
                        (sync_run_id,),
                    )

                cur.execute(
                    """
                    INSERT INTO contribuyentes (
                        rnc,
                        razon_social,
                        actividad_economica,
                        fecha_inicio_operaciones,
                        estatus,
                        regimen,
                        en_padron,
                        updated_at
                    )
                    SELECT
                        rnc,
                        razon_social,
                        actividad_economica,
                        fecha_inicio_operaciones,
                        estatus,
                        regimen,
                        TRUE,
                        NOW()
                    FROM _batch_in
                    ON CONFLICT (rnc) DO UPDATE SET
                        razon_social = EXCLUDED.razon_social,
                        actividad_economica = EXCLUDED.actividad_economica,
                        fecha_inicio_operaciones = EXCLUDED.fecha_inicio_operaciones,
                        estatus = EXCLUDED.estatus,
                        regimen = EXCLUDED.regimen,
                        en_padron = TRUE,
                        updated_at = NOW()
                    WHERE (
                        contribuyentes.razon_social,
                        contribuyentes.actividad_economica,
                        contribuyentes.fecha_inicio_operaciones,
                        contribuyentes.estatus,
                        contribuyentes.regimen,
                        contribuyentes.en_padron
                    ) IS DISTINCT FROM (
                        EXCLUDED.razon_social,
                        EXCLUDED.actividad_economica,
                        EXCLUDED.fecha_inicio_operaciones,
                        EXCLUDED.estatus,
                        EXCLUDED.regimen,
                        TRUE
                    );
                    """
                )
                changed = max(cur.rowcount, 0)

        return len(rows), changed

    def finalize_sync_tracking(
        self,
        sync_run_id: int | None,
        processed_count: int,
        record_history: bool = False,
    ) -> int:
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) AS active_total FROM contribuyentes WHERE en_padron;"
                )
                active_row = cur.fetchone()
                prev_active = int(active_row["active_total"]) if active_row else 0

                if prev_active > 1000 and processed_count < prev_active * 0.5:
                    logger.warning(
                        "Sincronización parcial detectada (%d vs %d activos previos); "
                        "se omite marcar bajas del padrón.",
                        processed_count,
                        prev_active,
                    )
                    cur.execute("TRUNCATE TABLE sync_seen_rncs;")
                    return 0

                if record_history:
                    cur.execute(
                        """
                        WITH bajas AS (
                            UPDATE contribuyentes c
                            SET en_padron = FALSE,
                                updated_at = NOW()
                            WHERE c.en_padron
                              AND NOT EXISTS (
                                  SELECT 1 FROM sync_seen_rncs s WHERE s.rnc = c.rnc
                              )
                            RETURNING
                                c.rnc,
                                c.razon_social,
                                c.actividad_economica,
                                c.estatus,
                                c.regimen
                        )
                        INSERT INTO contribuyente_historial (
                            rnc,
                            sync_run_id,
                            cambio_tipo,
                            razon_social_anterior,
                            actividad_anterior,
                            estatus_anterior,
                            regimen_anterior
                        )
                        SELECT
                            rnc,
                            %s,
                            'BAJA_PADRON',
                            razon_social,
                            actividad_economica,
                            estatus,
                            regimen
                        FROM bajas;
                        """,
                        (sync_run_id,),
                    )
                else:
                    cur.execute(
                        """
                        UPDATE contribuyentes c
                        SET en_padron = FALSE,
                            updated_at = NOW()
                        WHERE c.en_padron
                          AND NOT EXISTS (
                              SELECT 1 FROM sync_seen_rncs s WHERE s.rnc = c.rnc
                          );
                        """
                    )

                bajas_count = max(cur.rowcount, 0)
                cur.execute("TRUNCATE TABLE sync_seen_rncs;")
                return bajas_count

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

    def is_sync_locked(self) -> bool:
        """Verifica en pg_locks si algún proceso mantiene el bloqueo de sincronización."""
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM pg_locks
                        WHERE locktype = 'advisory'
                          AND objsubid = 1
                          AND ((classid::bigint << 32) + objid::bigint) = %s
                          AND granted
                    ) AS locked;
                    """,
                    (_SYNC_ADVISORY_LOCK_KEY,),
                )
                row = cur.fetchone()
                return bool(row and row["locked"])

    def cleanup_stale_sync_runs(self) -> int:
        """Marca como interrumpidas las filas 'running' cuando no hay lock activo."""
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_try_advisory_lock(%s) AS locked;",
                    (_SYNC_ADVISORY_LOCK_KEY,),
                )
                row = cur.fetchone()
                if not (row and row["locked"]):
                    return 0
                try:
                    cur.execute(
                        """
                        UPDATE sync_runs
                        SET
                            status = 'interrupted',
                            error_message = COALESCE(
                                error_message,
                                'Sincronización interrumpida por reinicio del servicio.'
                            ),
                            finished_at = NOW()
                        WHERE status = 'running';
                        """
                    )
                    return max(cur.rowcount, 0)
                finally:
                    cur.execute(
                        "SELECT pg_advisory_unlock(%s);",
                        (_SYNC_ADVISORY_LOCK_KEY,),
                    )

    def analyze_catalog(self) -> None:
        """Actualiza estadísticas del planificador de consultas tras cambios masivos."""
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("ANALYZE contribuyentes;")

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
