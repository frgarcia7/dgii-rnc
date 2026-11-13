"""Implementación PostgreSQL de ContribuyenteRepository con psycopg2 directo."""

from __future__ import annotations

import logging
from typing import Any

from psycopg2.extras import execute_values

from app.domain.entities import Contribuyente
from app.infrastructure.db.connection import PostgresConnectionManager

logger = logging.getLogger(__name__)

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
    updated_at = NOW();
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


class PostgresContribuyenteRepository:
    """Repositorio PostgreSQL usando psycopg2, pg_trgm y execute_values."""

    def __init__(self, conn_manager: PostgresConnectionManager) -> None:
        self._conn_manager = conn_manager

    def init_schema(self) -> None:
        """Crea la extensión pg_trgm, tabla contribuyentes, migraciones e índice GIN."""
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
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
                        ADD COLUMN IF NOT EXISTS fecha_inicio_operaciones VARCHAR(50) NOT NULL DEFAULT '';
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
                    CREATE INDEX IF NOT EXISTS idx_contribuyentes_razon_social_trgm
                    ON contribuyentes USING gin (razon_social gin_trgm_ops);
                    """
                )

        logger.info(
            "Base de datos inicializada correctamente (esquema CSV e índice pg_trgm listos)."
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
                    """
                    SELECT
                        rnc,
                        razon_social,
                        actividad_economica,
                        fecha_inicio_operaciones,
                        estatus,
                        regimen,
                        updated_at
                    FROM contribuyentes
                    WHERE rnc = %s;
                    """,
                    (rnc,),
                )
                row = cur.fetchone()
                return _row_to_entity(dict(row)) if row else None

    def search_by_razon_social(
        self, query: str, limit: int, offset: int
    ) -> list[Contribuyente]:
        pattern = f"%{_escape_like(query)}%"
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        rnc,
                        razon_social,
                        actividad_economica,
                        fecha_inicio_operaciones,
                        estatus,
                        regimen,
                        updated_at
                    FROM contribuyentes
                    WHERE razon_social ILIKE %s ESCAPE '\\'
                    ORDER BY razon_social ASC
                    LIMIT %s OFFSET %s;
                    """,
                    (pattern, limit, offset),
                )
                return [_row_to_entity(dict(row)) for row in cur.fetchall()]

    def upsert_batch(self, items: list[Contribuyente]) -> int:
        if not items:
            return 0

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
        with self._conn_manager.connection() as conn:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    UPSERT_SQL,
                    rows,
                    template="(%s, %s, %s, %s, %s, %s, NOW())",
                    page_size=5_000,
                )
        return len(rows)

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
