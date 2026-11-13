"""Pruebas de capa HTTP/FastAPI (routers, validación, CORS, API Key y UI web)."""

from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import dataclass
from urllib.parse import urlencode

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
from app.core.config import ApiSettings, DatabaseSettings, Settings, SyncSettings
from app.domain.entities import Contribuyente
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
            "client": ("127.0.0.1", 12345),
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
):
    repo = InMemoryRepo(initial)
    source = InMemorySource([initial or []])
    sync_uc = SyncRncCatalogUseCase(repo, source)
    resolved_api = api_settings or ApiSettings()
    settings = Settings(
        app_title="DGII RNC Test",
        app_version="1.1.0",
        log_level="WARNING",
        database=DatabaseSettings("localhost", 5432, "u", "p", "db"),
        sync=SyncSettings(enable_scheduler=False),
        api=resolved_api,
    )
    container = AppContainer(
        conn_manager=None,
        scheduler=None,
        init_uc=InitializeCatalogUseCase(repo, sync_uc),
        get_by_rnc_uc=GetContribuyenteByRncUseCase(repo),
        validate_rnc_uc=ValidateRncUseCase(repo),
        batch_lookup_uc=BatchLookupRncUseCase(
            repo, max_items=resolved_api.batch_max_items
        ),
        search_uc=SearchContribuyentesUseCase(repo),
        stats_uc=GetStatsUseCase(repo, sync_uc),
        sync_uc=sync_uc,
        health_uc=CheckHealthUseCase(repo),
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
