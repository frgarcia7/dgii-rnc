"""Gestión del pool de conexiones PostgreSQL con psycopg2 y RealDictCursor."""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.pool import ThreadedConnectionPool

from app.core.config import DatabaseSettings

logger = logging.getLogger(__name__)


class PostgresConnectionManager:
    """Administra un ThreadedConnectionPool con RealDictCursor sin ORMs."""

    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings
        self._pool: ThreadedConnectionPool | None = None

    def _get_pool(self) -> ThreadedConnectionPool:
        if self._pool is None:
            self._pool = ThreadedConnectionPool(
                minconn=self._settings.pool_min,
                maxconn=self._settings.pool_max,
                host=self._settings.host,
                port=self._settings.port,
                user=self._settings.user,
                password=self._settings.password,
                dbname=self._settings.name,
                cursor_factory=RealDictCursor,
            )
        return self._pool

    @contextmanager
    def connection(self) -> Generator[psycopg2.extensions.connection, None, None]:
        """Entrega una conexión del pool con commit automático y rollback en errores."""
        pool = self._get_pool()
        conn = pool.getconn()
        close_conn = False
        try:
            yield conn
            if not conn.closed:
                conn.commit()
        except Exception:
            if not conn.closed:
                conn.rollback()
            close_conn = bool(conn.closed)
            raise
        finally:
            pool.putconn(conn, close=close_conn)

    def close(self) -> None:
        """Cierra todas las conexiones activas del pool."""
        if self._pool is not None:
            self._pool.closeall()
            self._pool = None
            logger.info("Pool de conexiones a PostgreSQL cerrado.")

