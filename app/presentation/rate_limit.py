"""Rate limiting en memoria por IP/API Key con soporte seguro de proxy headers."""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from app.core.config import Settings


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """Resultado de evaluación de cuota para una solicitud."""

    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int
    retry_after: int = 0

    def to_headers(self) -> dict[str, str]:
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(self.remaining),
            "X-RateLimit-Reset": str(self.reset_seconds),
        }
        if not self.allowed:
            headers["Retry-After"] = str(max(1, self.retry_after))
        return headers


class SlidingWindowRateLimiter:
    """Limitador por ventana deslizante thread-safe con poda acotada de memoria."""

    def __init__(
        self,
        window_seconds: int = 60,
        max_keys: int = 20_000,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._window_seconds = max(1, window_seconds)
        self._max_keys = max(100, max_keys)
        self._clock = clock or time.monotonic
        self._lock = threading.Lock()
        # (bucket, subject) -> (window_start_sec, current_count, prev_count)
        self._buckets: dict[tuple[str, str], tuple[int, int, int]] = {}

    def check(self, bucket: str, subject: str, limit: int) -> RateLimitDecision:
        """Evalúa e incrementa el contador para (bucket, subject)."""
        now = self._clock()
        win_size = self._window_seconds
        current_window = int(now // win_size) * win_size
        elapsed = max(0.0, now - current_window)
        prev_weight = max(0.0, 1.0 - (elapsed / win_size))
        reset_seconds = max(1, math.ceil(win_size - elapsed))
        key = (bucket, subject)

        with self._lock:
            entry = self._buckets.get(key)
            if entry is None:
                prev_count = 0
                current_count = 0
            else:
                entry_window, entry_curr, entry_prev = entry
                if entry_window == current_window:
                    current_count = entry_curr
                    prev_count = entry_prev
                elif entry_window == current_window - win_size:
                    current_count = 0
                    prev_count = entry_curr
                else:
                    current_count = 0
                    prev_count = 0

            projected = (prev_count * prev_weight) + current_count + 1.0
            if projected > limit:
                self._buckets[key] = (current_window, current_count, prev_count)
                return RateLimitDecision(
                    allowed=False,
                    limit=limit,
                    remaining=0,
                    reset_seconds=reset_seconds,
                    retry_after=reset_seconds,
                )

            new_count = current_count + 1
            self._buckets[key] = (current_window, new_count, prev_count)
            if len(self._buckets) > self._max_keys:
                self._prune_expired_locked(current_window - win_size)

            used = (prev_count * prev_weight) + new_count
            remaining = max(0, int(limit - used))
            return RateLimitDecision(
                allowed=True,
                limit=limit,
                remaining=remaining,
                reset_seconds=reset_seconds,
            )

    def _prune_expired_locked(self, min_valid_window: int) -> None:
        stale_keys = [
            key
            for key, (win_start, _, _) in self._buckets.items()
            if win_start < min_valid_window
        ]
        for key in stale_keys:
            del self._buckets[key]

        if len(self._buckets) > self._max_keys:
            overflow = len(self._buckets) - (self._max_keys // 2)
            for key in list(self._buckets.keys())[:overflow]:
                del self._buckets[key]


def _parse_valid_ip(raw_value: str | None) -> str | None:
    if not raw_value:
        return None
    candidate = raw_value.strip()
    if not candidate:
        return None
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return None


def _extract_forwarded_ip(request: Request) -> str | None:
    cf_ip = _parse_valid_ip(request.headers.get("cf-connecting-ip"))
    if cf_ip:
        return cf_ip

    real_ip = _parse_valid_ip(request.headers.get("x-real-ip"))
    if real_ip:
        return real_ip

    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        for part in forwarded_for.split(","):
            parsed = _parse_valid_ip(part)
            if parsed:
                return parsed
    return None


def extract_client_ip(request: Request, trust_proxy_headers: bool) -> str:
    """Obtiene la IP del cliente, leyendo cabeceras proxy solo si está habilitado."""
    if trust_proxy_headers:
        forwarded_ip = _extract_forwarded_ip(request)
        if forwarded_ip:
            return forwarded_ip

    if request.client and request.client.host:
        parsed = _parse_valid_ip(request.client.host)
        return parsed or request.client.host

    return "unknown"


def is_loopback_request(request: Request) -> bool:
    """Verifica que la solicitud provenga realmente de loopback (sin proxy externo)."""
    client_host = request.client.host if request.client else None
    if not client_host:
        return False

    if client_host != "testclient":
        try:
            if not ipaddress.ip_address(client_host).is_loopback:
                return False
        except ValueError:
            return False

    # Si pasó por proxy inverso (aunque TRUST_PROXY_HEADERS=false), exigir origen loopback.
    for header_name in ("cf-connecting-ip", "x-real-ip", "x-forwarded-for", "forwarded"):
        raw_val = request.headers.get(header_name)
        if raw_val and raw_val.strip():
            forwarded_ip = _extract_forwarded_ip(request)
            if not forwarded_ip:
                return False
            try:
                if not ipaddress.ip_address(forwarded_ip).is_loopback:
                    return False
            except ValueError:
                return False

    return True


def _extract_provided_key(request: Request) -> str | None:
    x_api_key = request.headers.get("x-api-key")
    if x_api_key and x_api_key.strip():
        return x_api_key.strip()
    auth = request.headers.get("authorization")
    if auth and auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token or None
    return None


def _is_privileged_api_key(request: Request, settings: Settings) -> bool:
    provided = _extract_provided_key(request)
    if not provided:
        return False
    for expected in (settings.api.api_key, settings.api.sync_api_key):
        if expected and hmac.compare_digest(provided, expected):
            return True
    return False


def enforce_rate_limit(request: Request, bucket: str) -> None:
    """Aplica rate limiting al bucket solicitado y registra headers X-RateLimit-*."""
    settings: Settings = request.app.state.settings
    if not settings.rate_limit.enabled:
        return

    limiter: SlidingWindowRateLimiter | None = getattr(
        request.app.state, "rate_limiter", None
    )
    if limiter is None:
        return

    limits_by_bucket = {
        "lookup": settings.rate_limit.lookup_per_minute,
        "search": settings.rate_limit.search_per_minute,
        "batch": settings.rate_limit.batch_per_minute,
        "sync": settings.rate_limit.sync_per_minute,
    }
    base_limit = limits_by_bucket.get(bucket, settings.rate_limit.lookup_per_minute)

    client_ip = extract_client_ip(
        request, trust_proxy_headers=settings.rate_limit.trust_proxy_headers
    )
    if _is_privileged_api_key(request, settings):
        effective_limit = base_limit * settings.rate_limit.api_key_multiplier
        key_digest = hashlib.sha256(
            (_extract_provided_key(request) or "").encode("utf-8")
        ).hexdigest()[:12]
        subject = f"key:{key_digest}:{client_ip}"
    else:
        effective_limit = base_limit
        subject = f"ip:{client_ip}"

    decision = limiter.check(bucket=bucket, subject=subject, limit=effective_limit)
    headers = decision.to_headers()
    request.state.rate_limit_headers = headers

    if not decision.allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "Límite de consultas por minuto excedido. "
                f"Intenta nuevamente en {decision.retry_after} segundos."
            ),
            headers=headers,
        )
