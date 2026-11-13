"""Utilidades de caché en memoria thread-safe con TTL y política LRU."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Generic, TypeVar

K = TypeVar("K")
V = TypeVar("V")


class TtlLruCache(Generic[K, V]):
    """Caché LRU en memoria con expiración por TTL y soporte para valores None."""

    def __init__(self, maxsize: int = 2048, ttl_seconds: int = 300) -> None:
        self._maxsize = max(0, maxsize)
        self._ttl_seconds = max(0, ttl_seconds)
        self._entries: OrderedDict[K, tuple[float, V]] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        return self._maxsize > 0 and self._ttl_seconds > 0

    def get_entry(self, key: K) -> tuple[bool, V | None]:
        """Retorna (hit, value). Distingue entre cache miss y un valor None cacheado."""
        if not self.enabled:
            return False, None

        now = time.monotonic()
        with self._lock:
            item = self._entries.get(key)
            if item is None:
                return False, None

            expires_at, value = item
            if expires_at <= now:
                del self._entries[key]
                return False, None

            self._entries.move_to_end(key)
            return True, value

    def set(self, key: K, value: V) -> None:
        """Almacena un valor en caché respetando maxsize y TTL."""
        if not self.enabled:
            return

        expires_at = time.monotonic() + self._ttl_seconds
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
            self._entries[key] = (expires_at, value)
            while len(self._entries) > self._maxsize:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        """Invalida todas las entradas de la caché."""
        with self._lock:
            self._entries.clear()

