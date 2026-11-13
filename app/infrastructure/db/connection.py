"""Gestión del pool de conexiones PostgreSQL con psycopg2 y RealDictCursor."""

from __future__ import annotations

import logging
import threading
from contextlib import contextmanager
from typing import Generator

import psycopg2
from psycopg2.errors import QueryCanceled
from psycopg2.extras import RealDictCursor
from psycopg2.pool import PoolError, ThreadedConnectionPool

from app.core.config import DatabaseSettings
from app.domain.exceptions import DatabaseTimeoutError, DatabaseUnavailableError

logger = logging.getLogger(__name__)


class PostgresConnectionManager:
    """Administra un ThreadedConnectionPool acotado con espera y recuperación de fallos."""

    def __init__(self, settings: DatabaseSettings) -> None:
        self._settings = settings
        self._pool: ThreadedConnectionPool | None = None
        self._init_lock = threading.Lock()
        self._semaphore = threading.BoundedSemaphore(self._settings.pool_max)

    @property
    def statement_timeout_ms(self) -> int:
        """Timeout configurado para consultas de usuario."""
        return self._settings.statement_timeout_ms

    def _get_pool(self) -> ThreadedConnectionPool:
        if self._pool is None:
            with self._init_lock:
                if self._pool is None:
                    self._pool = ThreadedConnectionPool(
                        minconn=self._settings.pool_min,
                        maxconn=self._settings.pool_max,
                        host=self._settings.host,
                        port=self._settings.port,
                        user=self._settings.user,
                        password=self._settings.password,
                        dbname=self._settings.name,
                        connect_timeout=max(
                            2, int(self._settings.pool_timeout_seconds)
                        ),
                        cursor_factory=RealDictCursor,
                    )
        return self._pool

    @contextmanager
    def connection(self) -> Generator[psycopg2.extensions.connection, None, None]:
        """Entrega una conexión del pool esperando hasta pool_timeout_seconds."""
        if not self._semaphore.acquire(timeout=self._settings.pool_timeout_seconds):
            raise DatabaseUnavailableError(
                "El servidor está temporalmente saturado. Intenta nuevamente en unos segundos."
            )

        pool: ThreadedConnectionPool | None = None
        conn: psycopg2.extensions.connection | None = None
        close_conn = False
        try:
            pool = self._get_pool()
            conn = pool.getconn()
            if conn.closed:
                pool.putconn(conn, close=True)
                conn = pool.getconn()

            yield conn
            if not conn.closed:
                conn.commit()
        except QueryCanceled as exc:
            if conn is not None and not conn.closed:
                conn.rollback()
            raise DatabaseTimeoutError() from exc
        except (psycopg2.OperationalError, psycopg2.InterfaceError, PoolError) as exc:
            if conn is not None and not conn.closed:
                try:
                    conn.rollback()
                except Exception:
                    pass
            close_conn = True
            raise DatabaseUnavailableError() from exc
        except Exception:
            if conn is not None and not conn.closed:
                conn.rollback()
            close_conn = bool(conn is not None and conn.closed)
            raise
        finally:
            if pool is not None and conn is not None:
                try:
                    pool.putconn(conn, close=close_conn)
                except Exception:
                    logger.warning("Error devolviendo conexión al pool.", exc_info=True)
            self._semaphore.release()

    def close(self) -> None:
        """Cierra todas las conexiones activas del pool."""
        with self._init_lock:
            if self._pool is not None:
                self._pool.closeall()
                self._pool = None
                logger.info("Pool de conexiones a PostgreSQL cerrado.")
