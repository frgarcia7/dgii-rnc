"""Pruebas de capa HTTP/FastAPI (routers, validación, CORS, API Key y UI web)."""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import dataclass
from urllib.parse import urlencode

from app.application.cache import TtlLruCache
from app.application.use_cases import (
    BatchLookupRncUseCase,
    CheckHealthUseCase,
    GetContribuyenteByRncUseCase,
    GetStatsUseCase,
    InitializeCatalogUseCase,
    SearchContribuyentesUseCase,
    SyncRncCatalogUseCase,
    ValidateRncUseCase,
)
from app.core.config import (
    ApiSettings,
    DatabaseSettings,
    RateLimitSettings,
    Settings,
    SyncSettings,
)
from app.domain.entities import CatalogStats, Contribuyente
from app.domain.exceptions import DatabaseTimeoutError, DatabaseUnavailableError
from app.main import AppContainer, create_app
from tests.fakes import InMemoryRepo, InMemorySource


@dataclass
class AsgiResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes

    def json(self) -> dict:
        return json.loads(self.body.decode("utf-8"))

    @property
    def text(self) -> str:
        return self.body.decode("utf-8")


def call_asgi(
    app,
    method: str,
    path: str,
    query: dict[str, str] | None = None,
    json_body: dict | None = None,
    headers: dict[str, str] | None = None,
    client: tuple[str, int] = ("127.0.0.1", 12345),
) -> AsgiResponse:
    """Ejecuta una petición HTTP directa contra el callable ASGI de FastAPI."""

    async def _run() -> AsgiResponse:
        raw_body = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
        req_headers: list[tuple[bytes, bytes]] = []
        if json_body is not None:
            req_headers.append((b"content-type", b"application/json"))
        for k, v in (headers or {}).items():
            req_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))

        query_bytes = urlencode(query or {}).encode("latin-1")
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": method.upper(),
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": query_bytes,
            "headers": req_headers,
            "client": client,
            "server": ("testserver", 80),
        }

        sent = False
        status_code = 500
        resp_headers: dict[str, str] = {}
        chunks: list[bytes] = []

        async def receive() -> dict:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": raw_body, "more_body": False}
            return {"type": "http.disconnect"}

        async def send(message: dict) -> None:
            nonlocal status_code, resp_headers
            if message["type"] == "http.response.start":
                status_code = message["status"]
                resp_headers = {
                    k.decode("latin-1").lower(): v.decode("latin-1")
                    for k, v in message.get("headers", [])
                }
            elif message["type"] == "http.response.body":
                chunks.append(message.get("body", b""))

        await app(scope, receive, send)
        return AsgiResponse(
            status_code=status_code,
            headers=resp_headers,
            body=b"".join(chunks),
        )

    return asyncio.run(_run())


def _build_test_app(
    initial: list[Contribuyente] | None = None,
    api_settings: ApiSettings | None = None,
    rate_limit_settings: RateLimitSettings | None = None,
):
    repo = InMemoryRepo(initial)
    source = InMemorySource([initial or []])
    resolved_api = api_settings or ApiSettings()
    resolved_rl = rate_limit_settings or RateLimitSettings(enabled=False)
    lookup_cache: TtlLruCache[str, Contribuyente | None] = TtlLruCache(
        maxsize=resolved_api.lookup_cache_size,
        ttl_seconds=resolved_api.lookup_cache_ttl_seconds,
    )
    stats_cache: TtlLruCache[str, CatalogStats] = TtlLruCache(
        maxsize=1, ttl_seconds=resolved_api.stats_cache_ttl_seconds
    )
    count_cache: TtlLruCache[str, int] = TtlLruCache(
        maxsize=1, ttl_seconds=resolved_api.stats_cache_ttl_seconds
    )
    sync_uc = SyncRncCatalogUseCase(
        repo,
        source,
        on_catalog_updated=(lookup_cache.clear, stats_cache.clear, count_cache.clear),
    )
    settings = Settings(
        app_title="DGII RNC Test",
        app_version="1.1.0",
        log_level="WARNING",
        database=DatabaseSettings("localhost", 5432, "u", "p", "db"),
        sync=SyncSettings(enable_scheduler=False),
        api=resolved_api,
        rate_limit=resolved_rl,
    )
    container = AppContainer(
        conn_manager=None,
        scheduler=None,
        init_uc=InitializeCatalogUseCase(repo, sync_uc),
        get_by_rnc_uc=GetContribuyenteByRncUseCase(repo, lookup_cache=lookup_cache),
        validate_rnc_uc=ValidateRncUseCase(repo, lookup_cache=lookup_cache),
        batch_lookup_uc=BatchLookupRncUseCase(
            repo, max_items=resolved_api.batch_max_items
        ),
        search_uc=SearchContribuyentesUseCase(repo),
        stats_uc=GetStatsUseCase(repo, sync_uc, stats_cache=stats_cache),
        sync_uc=sync_uc,
        health_uc=CheckHealthUseCase(repo, count_cache=count_cache),
    )
    return create_app(settings=settings, container=container, run_startup=False), repo


class TestApiEndpoints(unittest.TestCase):
    def setUp(self) -> None:
        self.sample = Contribuyente(
            rnc="130663041",
            razon_social="CONTRATISTAS DE INFRAESTRUCTURAS SRL",
            actividad_economica="INSTALACIONES ELÉCTRICAS",
            fecha_inicio_operaciones="01/12/2009",
            estatus="ACTIVO",
            regimen="NORMAL",
        )
        self.second = Contribuyente(
            rnc="101010101",
            razon_social="CLÍNICA DEL CARIBE SRL",
            actividad_economica="SERVICIOS MÉDICOS",
            fecha_inicio_operaciones="05/03/2018",
            estatus="SUSPENDIDO",
            regimen="RST",
        )
        self.app, self.repo = _build_test_app([self.sample, self.second])

    def test_web_ui_and_swagger_redirect(self) -> None:
        home = call_asgi(self.app, "GET", "/")
        self.assertEqual(home.status_code, 200)
        self.assertIn("text/html", home.headers["content-type"])
        self.assertIn("Consulta RNC / Cédula", home.text)

        redir = call_asgi(self.app, "GET", "/swagger")
        self.assertEqual(redir.status_code, 307)
        self.assertEqual(redir.headers["location"], "/docs")

    def test_health_endpoint_ok_and_503(self) -> None:
        ok = call_asgi(self.app, "GET", "/health")
        self.assertEqual(ok.status_code, 200)
        data = ok.json()
        self.assertTrue(data["catalog_ready"])
        self.assertEqual(data["total_contribuyentes"], 2)

        self.repo.healthy = False
        down = call_asgi(self.app, "GET", "/health")
        self.assertEqual(down.status_code, 503)

    def test_rnc_lookup_v1_and_legacy_alias(self) -> None:
        for prefix in ("/api/v1", "/api"):
            res = call_asgi(self.app, "GET", f"{prefix}/rnc/130-66304-1")
            self.assertEqual(res.status_code, 200)
            body = res.json()
            self.assertEqual(body["rnc"], "130663041")
            self.assertEqual(body["rnc_formateado"], "130-66304-1")
            self.assertEqual(body["tipo"], "RNC")
            self.assertIn("max-age=300", res.headers.get("cache-control", ""))

        not_found = call_asgi(self.app, "GET", "/api/v1/rnc/999999999")
        self.assertEqual(not_found.status_code, 404)

        invalid = call_asgi(self.app, "GET", "/api/v1/rnc/abc")
        self.assertEqual(invalid.status_code, 400)

    def test_validate_rnc_and_batch_endpoints(self) -> None:
        val = call_asgi(self.app, "GET", "/api/v1/rnc/130-66304-1/validar")
        self.assertEqual(val.status_code, 200)
        data = val.json()
        self.assertTrue(data["formato_valido"])
        self.assertTrue(data["digito_verificador_valido"])
        self.assertTrue(data["registrado"])
        self.assertEqual(data["contribuyente"]["rnc"], "130663041")

        batch = call_asgi(
            self.app,
            "POST",
            "/api/v1/rnc/batch",
            json_body={"rncs": ["130-66304-1", "999999999", "XYZ"]},
        )
        self.assertEqual(batch.status_code, 200)
        bdata = batch.json()
        self.assertEqual(bdata["requested"], 3)
        self.assertEqual(bdata["found_count"], 1)
        self.assertEqual(bdata["not_found"], ["999999999"])
        self.assertEqual(bdata["invalid"], ["XYZ"])

    def test_search_with_unaccent_filters_and_pagination_metadata(self) -> None:
        res = call_asgi(
            self.app,
            "GET",
            "/api/v1/buscar",
            query={"query": "clinica", "estatus": "SUSPENDIDO"},
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["total"], 1)
        self.assertEqual(body["count"], 1)
        self.assertFalse(body["has_more"])
        self.assertEqual(body["results"][0]["rnc"], "101010101")

        too_short = call_asgi(self.app, "GET", "/api/v1/buscar", query={"query": "a"})
        self.assertEqual(too_short.status_code, 422)

    def test_stats_and_sync_protection_and_cors(self) -> None:
        stats = call_asgi(self.app, "GET", "/api/v1/stats")
        self.assertEqual(stats.status_code, 200)
        self.assertEqual(stats.json()["total_contribuyentes"], 2)

        cors_preflight = call_asgi(
            self.app,
            "OPTIONS",
            "/api/v1/rnc/130663041",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        self.assertEqual(cors_preflight.status_code, 200)
        self.assertEqual(
            cors_preflight.headers.get("access-control-allow-origin"), "*"
        )

        # Instancia con API_KEY y SYNC_API_KEY activas
        secured_app, _ = _build_test_app(
            [self.sample],
            api_settings=ApiSettings(api_key="read-key", sync_api_key="admin-key"),
        )
        self.assertEqual(
            call_asgi(secured_app, "GET", "/api/v1/rnc/130663041").status_code, 401
        )
        self.assertEqual(
            call_asgi(
                secured_app,
                "GET",
                "/api/v1/rnc/130663041",
                headers={"X-API-Key": "read-key"},
            ).status_code,
            200,
        )
        self.assertEqual(
            call_asgi(
                secured_app,
                "POST",
                "/api/v1/sync",
                headers={"X-API-Key": "read-key"},
            ).status_code,
            401,
        )
        self.assertEqual(
            call_asgi(
                secured_app,
                "POST",
                "/api/v1/sync",
                headers={"Authorization": "Bearer admin-key"},
            ).status_code,
            202,
        )

        disabled_sync_app, _ = _build_test_app(
            [self.sample], api_settings=ApiSettings(enable_manual_sync=False)
        )
        self.assertEqual(
            call_asgi(disabled_sync_app, "POST", "/api/v1/sync").status_code, 403
        )

        # Sync status y conflicto 409 cuando ya está corriendo
        status_resp = call_asgi(self.app, "GET", "/api/v1/sync/status")
        self.assertEqual(status_resp.status_code, 200)
        self.assertEqual(status_resp.json()["status"], "never_run")

        with self.app.state.container.sync_uc._local_lock:
            conflict = call_asgi(self.app, "POST", "/api/v1/sync")
            self.assertEqual(conflict.status_code, 409)

    def test_security_headers_csp_and_private_cache_with_api_key(self) -> None:
        home = call_asgi(self.app, "GET", "/", headers={"X-Request-ID": "req-123"})
        self.assertEqual(home.headers.get("x-request-id"), "req-123")
        self.assertEqual(home.headers.get("x-content-type-options"), "nosniff")
        self.assertEqual(home.headers.get("x-frame-options"), "DENY")
        csp = home.headers.get("content-security-policy", "")
        self.assertIn("default-src 'self'", csp)
        self.assertNotIn("'unsafe-inline'", csp)
        self.assertIn("escapeCsvCell", home.text)

        health = call_asgi(self.app, "GET", "/health")
        self.assertEqual(health.json()["batch_max_items"], 200)

        secured_app, _ = _build_test_app(
            [self.sample],
            api_settings=ApiSettings(api_key="read-key"),
        )
        secured_resp = call_asgi(
            secured_app,
            "GET",
            "/api/v1/rnc/130663041",
            headers={"X-API-Key": "read-key"},
        )
        self.assertEqual(secured_resp.status_code, 200)
        self.assertIn("private", secured_resp.headers.get("cache-control", ""))
        self.assertIn("X-API-Key", secured_resp.headers.get("vary", ""))

    def test_rate_limiting_and_proxy_headers(self) -> None:
        rl_app, _ = _build_test_app(
            [self.sample],
            api_settings=ApiSettings(api_key="vip-key"),
            rate_limit_settings=RateLimitSettings(
                enabled=True,
                trust_proxy_headers=False,
                lookup_per_minute=2,
                search_per_minute=1,
                batch_per_minute=1,
                sync_per_minute=1,
                api_key_multiplier=2,
            ),
        )
        # Con trust_proxy_headers=False ignora X-Forwarded-For falso y agota cuota por IP real
        r1 = call_asgi(
            rl_app,
            "GET",
            "/api/v1/rnc/130663041",
            headers={"X-API-Key": "vip-key", "X-Forwarded-For": "1.1.1.1"},
        )
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r1.headers.get("x-ratelimit-limit"), "4")

        for expected_status in (200, 200, 200, 429):
            resp = call_asgi(
                rl_app,
                "GET",
                "/api/v1/rnc/130663041",
                headers={"X-API-Key": "vip-key", "X-Forwarded-For": "2.2.2.2"},
            )
            self.assertEqual(resp.status_code, expected_status)
            if expected_status == 429:
                self.assertIn("retry-after", resp.headers)

        # Con trust_proxy_headers=True separa clientes según X-Forwarded-For
        proxy_app, _ = _build_test_app(
            [self.sample],
            rate_limit_settings=RateLimitSettings(
                enabled=True,
                trust_proxy_headers=True,
                search_per_minute=1,
            ),
        )
        self.assertEqual(
            call_asgi(
                proxy_app,
                "GET",
                "/api/v1/buscar",
                query={"query": "infra"},
                headers={"X-Forwarded-For": "198.51.100.1"},
            ).status_code,
            200,
        )
        self.assertEqual(
            call_asgi(
                proxy_app,
                "GET",
                "/api/v1/buscar",
                query={"query": "infra"},
                headers={"X-Forwarded-For": "198.51.100.1"},
            ).status_code,
            429,
        )
        self.assertEqual(
            call_asgi(
                proxy_app,
                "GET",
                "/api/v1/buscar",
                query={"query": "infra"},
                headers={"X-Forwarded-For": "198.51.100.2"},
            ).status_code,
            200,
        )

    def test_sync_loopback_protection_and_status_sanitization(self) -> None:
        # Sin SYNC_API_KEY, solicitudes remotas o proxiadas desde fuera reciben 403
        remote_sync = call_asgi(
            self.app,
            "POST",
            "/api/v1/sync",
            client=("203.0.113.10", 54321),
        )
        self.assertEqual(remote_sync.status_code, 403)

        proxied_sync = call_asgi(
            self.app,
            "POST",
            "/api/v1/sync",
            headers={"X-Forwarded-For": "203.0.113.10"},
            client=("127.0.0.1", 54321),
        )
        self.assertEqual(proxied_sync.status_code, 403)

        # Con ALLOW_UNAUTHENTICATED_SYNC=true sí se permite
        open_app, _ = _build_test_app(
            [self.sample],
            api_settings=ApiSettings(allow_unauthenticated_sync=True),
        )
        self.assertEqual(
            call_asgi(
                open_app, "POST", "/api/v1/sync", client=("203.0.113.10", 54321)
            ).status_code,
            202,
        )

        # Sanitización de /sync/status para rutas internas y errores
        run_id = self.repo.record_sync_start("file:///etc/secret/padron.zip")
        self.repo.record_sync_finish(
            run_id=run_id,
            status="failed",
            processed=0,
            changed=0,
            elapsed_seconds=0.1,
            source="file:///etc/secret/padron.zip",
            error_message="psycopg2 internal path error",
        )
        public_status = call_asgi(
            self.app,
            "GET",
            "/api/v1/sync/status",
            client=("203.0.113.10", 54321),
        )
        self.assertEqual(public_status.status_code, 200)
        run_data = public_status.json()["run"]
        self.assertEqual(run_data["source"], "file://<local>")
        self.assertNotIn("psycopg2", run_data["error_message"])

        local_status = call_asgi(self.app, "GET", "/api/v1/sync/status")
        self.assertEqual(
            local_status.json()["run"]["source"], "file:///etc/secret/padron.zip"
        )
        self.assertIn("psycopg2", local_status.json()["run"]["error_message"])

    def test_database_error_and_unexpected_exception_handlers(self) -> None:
        self.repo.fail_next_error = DatabaseTimeoutError()
        timeout_resp = call_asgi(
            self.app, "GET", "/api/v1/buscar", query={"query": "infra"}
        )
        self.assertEqual(timeout_resp.status_code, 504)

        self.repo.fail_next_error = DatabaseUnavailableError()
        busy_resp = call_asgi(self.app, "GET", "/api/v1/rnc/130663041")
        self.assertEqual(busy_resp.status_code, 503)
        self.assertEqual(busy_resp.headers.get("retry-after"), "5")

        self.repo.fail_next_error = RuntimeError("sensitive internal trace")
        crash_resp = call_asgi(self.app, "GET", "/api/v1/rnc/130663041")
        self.assertEqual(crash_resp.status_code, 500)
        self.assertEqual(crash_resp.json()["detail"], "Error interno del servidor.")
        self.assertNotIn("sensitive", crash_resp.text)
