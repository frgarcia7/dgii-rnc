"""Pruebas unitarias de los casos de uso y reglas de dominio."""

from __future__ import annotations

import os
import time
import unittest
from unittest.mock import MagicMock, patch

from psycopg2 import OperationalError
from psycopg2.errors import QueryCanceled

from app.application.cache import TtlLruCache
from app.application.use_cases import (
    BatchLookupRncUseCase,
    CheckHealthUseCase,
    GetContribuyenteByRncUseCase,
    GetContribuyenteHistoryUseCase,
    GetStatsUseCase,
    InitializeCatalogUseCase,
    SearchContribuyentesUseCase,
    SyncRncCatalogUseCase,
    ValidateRncUseCase,
)
from app.core.config import DatabaseSettings, Settings
from app.domain.entities import (
    Contribuyente,
    format_rnc,
    is_valid_cedula_check_digit,
    is_valid_rnc_check_digit,
    validate_rnc_format,
)
from app.domain.exceptions import (
    ContribuyenteNotFoundError,
    DatabaseTimeoutError,
    DatabaseUnavailableError,
    InvalidInputError,
)
from app.infrastructure.db.connection import PostgresConnectionManager
from tests.fakes import InMemoryRepo, InMemorySource


class TestDomainValidation(unittest.TestCase):
    def test_rnc_modulo_11_check_digit_cases(self) -> None:
        # Resto regular (120 % 11 = 10 -> DV 1)
        self.assertTrue(is_valid_rnc_check_digit("130663041"))
        self.assertFalse(is_valid_rnc_check_digit("130663042"))
        # Resto == 1 -> DV 1
        self.assertTrue(is_valid_rnc_check_digit("101010101"))
        # Resto == 0 -> DV 2 (1*7 + 2*2 = 11 -> 11 % 11 == 0)
        self.assertTrue(is_valid_rnc_check_digit("100000022"))
        self.assertFalse(is_valid_rnc_check_digit("100000020"))
        # Ceros o longitud inválida
        self.assertFalse(is_valid_rnc_check_digit("000000000"))
        self.assertFalse(is_valid_rnc_check_digit("13066304"))

    def test_cedula_modulo_10_check_digit_cases(self) -> None:
        self.assertTrue(is_valid_cedula_check_digit("00100000017"))
        self.assertTrue(is_valid_cedula_check_digit("00100000058"))
        self.assertTrue(is_valid_cedula_check_digit("00200000040"))
        self.assertFalse(is_valid_cedula_check_digit("00100000018"))
        self.assertFalse(is_valid_cedula_check_digit("00000000000"))

    def test_format_and_validate_rnc_helper(self) -> None:
        self.assertEqual(format_rnc("130663041"), "130-66304-1")
        self.assertEqual(format_rnc("00100000017"), "001-0000001-7")

        val = validate_rnc_format(" 130-66304-1 ")
        self.assertEqual(val.rnc, "130663041")
        self.assertEqual(val.rnc_formateado, "130-66304-1")
        self.assertEqual(val.tipo, "RNC")
        self.assertTrue(val.formato_valido)
        self.assertTrue(val.digito_verificador_valido)


class TestUseCases(unittest.TestCase):
    def setUp(self) -> None:
        self.sample = Contribuyente(
            rnc="130663041",
            razon_social="CONTRATISTAS DE INFRAESTRUCTURAS DE TELEFONOS ELECTRICIDAD Y CABLE S R L",
            actividad_economica=(
                "EJECUCIÓN Y MANTENIMIENTO DE INSTALACIONES ELÉCTRICAS Y ELECTRÓNICAS N.C.P."
            ),
            fecha_inicio_operaciones="01/12/2009",
            estatus="SUSPENDIDO",
            regimen="NORMAL",
        )
        self.second = Contribuyente(
            rnc="101010101",
            razon_social="TELÉFONOS DEL CARIBE SRL",
            actividad_economica="COMERCIO AL POR MAYOR",
            fecha_inicio_operaciones="01/01/2015",
            estatus="ACTIVO",
            regimen="RST",
        )

    def test_get_by_rnc_normalizes_hyphens_and_spaces(self) -> None:
        repo = InMemoryRepo([self.sample])
        uc = GetContribuyenteByRncUseCase(repo)

        found = uc.execute(" 130-66304-1 ")
        self.assertEqual(found.rnc, "130663041")
        self.assertEqual(found.estatus, "SUSPENDIDO")

    def test_get_by_rnc_raises_not_found(self) -> None:
        uc = GetContribuyenteByRncUseCase(InMemoryRepo())
        with self.assertRaises(ContribuyenteNotFoundError):
            uc.execute("999999999")

    def test_get_by_rnc_raises_invalid_on_blank_or_alpha(self) -> None:
        uc = GetContribuyenteByRncUseCase(InMemoryRepo())
        for bad in ("  ---  ", "abc123", "123"):
            with self.assertRaises(InvalidInputError):
                uc.execute(bad)

    def test_validate_rnc_use_case(self) -> None:
        uc = ValidateRncUseCase(InMemoryRepo([self.sample]))
        validation, entity = uc.execute("130-66304-1")
        self.assertTrue(validation.digito_verificador_valido)
        self.assertIsNotNone(entity)
        assert entity is not None
        self.assertEqual(entity.rnc, "130663041")

        val_missing, missing_entity = uc.execute("101010101")
        self.assertTrue(val_missing.digito_verificador_valido)
        self.assertIsNone(missing_entity)

    def test_batch_lookup_use_case(self) -> None:
        uc = BatchLookupRncUseCase(InMemoryRepo([self.sample, self.second]), max_items=5)
        result = uc.execute(
            ["130-66304-1", "999999999", "130663041", "INVALIDO", "101010101"]
        )
        self.assertEqual([item.rnc for item in result.results], ["130663041", "101010101"])
        self.assertEqual(result.not_found, ["999999999"])
        self.assertEqual(result.invalid, ["INVALIDO"])

        with self.assertRaises(InvalidInputError):
            uc.execute(["101010101"] * 6)

    def test_search_ignores_accents_filters_and_paginates(self) -> None:
        uc = SearchContribuyentesUseCase(InMemoryRepo([self.sample, self.second]))

        res = uc.execute("  telefonos ", limit=1, offset=1)
        self.assertEqual(res.total, 2)
        self.assertEqual(len(res.items), 1)
        self.assertEqual(res.items[0].rnc, "130663041")

        filtered = uc.execute("telefonos", estatus="ACTIVO", regimen="RST")
        self.assertEqual(filtered.total, 1)
        self.assertEqual(filtered.items[0].rnc, "101010101")

    def test_search_raises_invalid_on_short_query(self) -> None:
        uc = SearchContribuyentesUseCase(InMemoryRepo())
        for bad in ("   ", "a"):
            with self.assertRaises(InvalidInputError):
                uc.execute(bad)

    def test_sync_and_initialize_populate_empty_repo_and_skip_unchanged(self) -> None:
        repo = InMemoryRepo()
        source = InMemorySource([[self.sample]])
        sync_uc = SyncRncCatalogUseCase(repo, source, batch_size=100)
        init_uc = InitializeCatalogUseCase(repo, sync_uc)

        init_uc.execute(run_async=False)

        self.assertTrue(repo.schema_initialized)
        self.assertEqual(repo.count(), 1)
        self.assertFalse(init_uc.is_initializing())
        self.assertTrue(CheckHealthUseCase(repo).execute())

        # Segunda ejecución sin cambios debe omitir escritura usando ETag/hash
        second_report = sync_uc.execute()
        self.assertEqual(second_report.status, "up_to_date")
        self.assertEqual(second_report.processed, 0)

        # Con force=True sí reprocesa, pero changed == 0 si no hubo cambios reales
        forced_report = sync_uc.execute(force=True)
        self.assertEqual(forced_report.status, "completed")
        self.assertEqual(forced_report.processed, 1)
        self.assertEqual(forced_report.changed, 0)

        stats = GetStatsUseCase(repo, sync_uc).execute()
        self.assertEqual(stats.total_contribuyentes, 1)
        self.assertEqual(stats.by_estatus["SUSPENDIDO"], 1)

    def test_initialize_async_populates_without_blocking(self) -> None:
        import time

        repo = InMemoryRepo()
        source = InMemorySource([[self.sample, self.second]])
        sync_uc = SyncRncCatalogUseCase(repo, source, batch_size=100)
        init_uc = InitializeCatalogUseCase(repo, sync_uc)

        init_uc.execute(run_async=True)
        deadline = time.monotonic() + 2.0
        while init_uc.is_initializing() and time.monotonic() < deadline:
            time.sleep(0.01)

        self.assertFalse(init_uc.is_initializing())
        self.assertEqual(repo.count(), 2)

    def test_sync_records_failure_and_respects_advisory_lock(self) -> None:
        repo = InMemoryRepo()
        repo.lock_busy = True
        sync_uc = SyncRncCatalogUseCase(repo, InMemorySource([[self.sample]]))
        locked_report = sync_uc.execute()
        self.assertEqual(locked_report.status, "already_running")

        repo.lock_busy = False
        failing_uc = SyncRncCatalogUseCase(
            repo, InMemorySource([], fail_with=RuntimeError("DGII timeout"))
        )
        with self.assertRaises(RuntimeError):
            failing_uc.execute()
        latest = failing_uc.get_latest_run()
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.status, "failed")
        self.assertIn("DGII timeout", latest.error_message or "")

    def test_settings_validates_bounds(self) -> None:
        with patch.dict(os.environ, {"SYNC_HOUR": "25"}):
            with self.assertRaises(ValueError):
                Settings.from_env()

        with patch.dict(os.environ, {"DB_POOL_MIN": "20", "DB_POOL_MAX": "5"}):
            with self.assertRaises(ValueError):
                Settings.from_env()

        with patch.dict(
            os.environ,
            {
                "ENABLE_SCHEDULER": "false",
                "CORS_ORIGINS": "http://localhost:3000, https://erp.local",
                "DB_POOL_TIMEOUT_SECONDS": "2.5",
                "RATE_LIMIT_SEARCH_PER_MINUTE": "45",
                "TRUST_PROXY_HEADERS": "true",
                "ALLOW_REMOTE_SYNC": "true",
            },
        ):
            cfg = Settings.from_env()
            self.assertFalse(cfg.sync.enable_scheduler)
            self.assertEqual(
                cfg.api.cors_origins, ("http://localhost:3000", "https://erp.local")
            )
            self.assertEqual(cfg.database.pool_timeout_seconds, 2.5)
            self.assertEqual(cfg.rate_limit.search_per_minute, 45)
            self.assertTrue(cfg.rate_limit.trust_proxy_headers)
            self.assertTrue(cfg.api.allow_remote_sync)

    def test_caches_reduce_db_reads_and_invalidate_on_sync(self) -> None:
        repo = InMemoryRepo([self.sample])
        lookup_cache: TtlLruCache[str, Contribuyente | None] = TtlLruCache(
            maxsize=10, ttl_seconds=60
        )
        stats_cache = TtlLruCache(maxsize=1, ttl_seconds=60)
        count_cache = TtlLruCache(maxsize=1, ttl_seconds=60)

        get_uc = GetContribuyenteByRncUseCase(repo, lookup_cache=lookup_cache)
        val_uc = ValidateRncUseCase(repo, lookup_cache=lookup_cache)
        sync_uc = SyncRncCatalogUseCase(
            repo,
            InMemorySource([[self.sample, self.second]]),
            on_catalog_updated=(lookup_cache.clear, stats_cache.clear, count_cache.clear),
        )
        stats_uc = GetStatsUseCase(repo, sync_uc, stats_cache=stats_cache)
        health_uc = CheckHealthUseCase(repo, count_cache=count_cache)

        # Lookup cache: 1 lectura DB para repetidos + negative hit para inexistente
        self.assertEqual(get_uc.execute("130663041").razon_social, self.sample.razon_social)
        _, validated_entity = val_uc.execute("130-66304-1")
        assert validated_entity is not None
        self.assertEqual(validated_entity.rnc, "130663041")
        self.assertEqual(repo.get_by_rnc_calls, 1)

        with self.assertRaises(ContribuyenteNotFoundError):
            get_uc.execute("999999999")
        with self.assertRaises(ContribuyenteNotFoundError):
            get_uc.execute("999999999")
        self.assertEqual(repo.get_by_rnc_calls, 2)

        # Stats y health snapshot cacheados
        stats_uc.execute()
        stats_uc.execute()
        self.assertEqual(repo.stats_calls, 1)

        health_uc.get_health_snapshot()
        health_uc.get_health_snapshot()
        self.assertEqual(repo.count_calls, 1)

        # Sincronización con cambios limpia cachés y llama analyze_catalog
        sync_uc.execute(force=True)
        self.assertEqual(repo.analyze_calls, 1)

        self.assertEqual(get_uc.execute("101010101").rnc, "101010101")
        self.assertEqual(repo.get_by_rnc_calls, 3)
        stats_uc.execute()
        self.assertEqual(repo.stats_calls, 2)
        _, ready, total = health_uc.get_health_snapshot()
        self.assertTrue(ready)
        self.assertEqual(total, 2)
        self.assertEqual(repo.count_calls, 2)

    def test_ttl_lru_cache_eviction_and_expiration(self) -> None:
        cache: TtlLruCache[str, int] = TtlLruCache(maxsize=2, ttl_seconds=1)
        cache.set("a", 1)
        cache.set("b", 2)
        self.assertEqual(cache.get_entry("a"), (True, 1))
        cache.set("c", 3)
        self.assertEqual(cache.get_entry("b"), (False, None))
        self.assertEqual(cache.get_entry("a"), (True, 1))

        time.sleep(1.05)
        self.assertEqual(cache.get_entry("a"), (False, None))

        disabled: TtlLruCache[str, int] = TtlLruCache(maxsize=0, ttl_seconds=60)
        disabled.set("x", 10)
        self.assertEqual(disabled.get_entry("x"), (False, None))

    def test_sync_rejects_empty_source_and_startup_cleans_stale_runs(self) -> None:
        repo = InMemoryRepo([self.sample])
        stale_id = repo.record_sync_start("dgii")
        assert stale_id is not None

        empty_sync_uc = SyncRncCatalogUseCase(repo, InMemorySource([[]]))
        init_uc = InitializeCatalogUseCase(repo, empty_sync_uc)
        init_uc.execute(run_async=False)

        latest_after_init = empty_sync_uc.get_latest_run()
        assert latest_after_init is not None
        self.assertEqual(latest_after_init.status, "interrupted")

        with self.assertRaises(ValueError):
            empty_sync_uc.execute()
        latest_after_empty = empty_sync_uc.get_latest_run()
        assert latest_after_empty is not None
        self.assertEqual(latest_after_empty.status, "failed")
        self.assertIsNone(repo.get_last_successful_sync())

    def test_search_supports_actividad_only_multitoken_fuzzy_and_rnc_prefix(self) -> None:
        ferreteria = Contribuyente(
            rnc="131999888",
            razon_social="FERRETERÍA HERMANOS OCHOA SRL",
            actividad_economica="VENTA DE MATERIALES DE CONSTRUCCIÓN",
            fecha_inicio_operaciones="01/03/2015",
            estatus="ACTIVO",
            regimen="NORMAL",
        )
        clinica = Contribuyente(
            rnc="132000111",
            razon_social="CLÍNICA DEL CORAZÓN SRL",
            actividad_economica="SERVICIOS MÉDICOS",
            fecha_inicio_operaciones="10/06/2016",
            estatus="ACTIVO",
            regimen="NORMAL",
        )
        repo = InMemoryRepo([self.sample, self.second, ferreteria, clinica])
        search_uc = SearchContribuyentesUseCase(repo)

        # Solo por actividad económica sin query
        by_act = search_uc.execute(raw_query=None, actividad="materiales construccion")
        self.assertEqual(by_act.total, 1)
        self.assertEqual(by_act.items[0].rnc, "131999888")

        # Multi-palabra fuera de secuencia exacta
        multi = search_uc.execute(raw_query="ferreteria ochoa")
        self.assertEqual(multi.total, 1)
        self.assertEqual(multi.items[0].rnc, "131999888")

        # Prefijo numérico de RNC
        rnc_pref = search_uc.execute(raw_query="13199")
        self.assertEqual(rnc_pref.total, 1)
        self.assertEqual(rnc_pref.items[0].rnc, "131999888")

        # Tolerancia tipográfica en fallback difuso
        fuzzy = search_uc.execute(raw_query="clinica del corason")
        self.assertEqual(fuzzy.total, 1)
        self.assertEqual(fuzzy.items[0].rnc, "132000111")

        # Sin query ni actividad o query de solo espacios -> InvalidInputError
        with self.assertRaises(InvalidInputError):
            search_uc.execute(raw_query=None, actividad=None)
        with self.assertRaises(InvalidInputError):
            search_uc.execute(raw_query="   ", actividad="construccion")

    def test_sync_history_lifecycle_and_partial_baja_guard(self) -> None:
        repo = InMemoryRepo()
        first_batch = [self.sample, self.second]
        sync_1 = SyncRncCatalogUseCase(
            repo, InMemorySource([first_batch], etag='"v1"', content_hash="h1")
        )
        sync_1.execute()

        # Carga inicial en BD vacía no genera miles/cientos de filas ALTA innecesarias
        hist_uc = GetContribuyenteHistoryUseCase(repo)
        current, events_1 = hist_uc.execute("130663041")
        self.assertIsNotNone(current)
        self.assertTrue(current.en_padron if current else False)
        self.assertEqual(events_1, [])

        # Segundo sync: sample cambia estatus, second sale (BAJA_PADRON), third entra (ALTA)
        updated_sample = Contribuyente(
            rnc=self.sample.rnc,
            razon_social=self.sample.razon_social,
            actividad_economica=self.sample.actividad_economica,
            fecha_inicio_operaciones=self.sample.fecha_inicio_operaciones,
            estatus="ACTIVO",
            regimen=self.sample.regimen,
        )
        third = Contribuyente(
            rnc="102030405",
            razon_social="NUEVA EMPRESA SRL",
            actividad_economica="SERVICIOS",
            fecha_inicio_operaciones="01/01/2025",
            estatus="ACTIVO",
            regimen="NORMAL",
        )
        sync_2 = SyncRncCatalogUseCase(
            repo,
            InMemorySource([[updated_sample, third]], etag='"v2"', content_hash="h2"),
        )
        report_2 = sync_2.execute()
        self.assertEqual(report_2.status, "completed")
        self.assertEqual(report_2.changed, 3)

        _, sample_hist = hist_uc.execute("130663041")
        self.assertEqual(len(sample_hist), 1)
        self.assertEqual(sample_hist[0].cambio_tipo, "ACTUALIZACION")
        self.assertEqual(sample_hist[0].estatus_anterior, "SUSPENDIDO")
        self.assertEqual(sample_hist[0].estatus_nuevo, "ACTIVO")

        dropped, dropped_hist = hist_uc.execute("101010101")
        self.assertIsNotNone(dropped)
        assert dropped is not None
        self.assertFalse(dropped.en_padron)
        self.assertEqual(dropped_hist[0].cambio_tipo, "BAJA_PADRON")

        # Tercer sync: second reingresa
        sync_3 = SyncRncCatalogUseCase(
            repo,
            InMemorySource(
                [[updated_sample, self.second, third]], etag='"v3"', content_hash="h3"
            ),
        )
        sync_3.execute()
        reentered, re_hist = hist_uc.execute("101010101")
        assert reentered is not None
        self.assertTrue(reentered.en_padron)
        self.assertEqual(re_hist[0].cambio_tipo, "REINGRESO")

        with self.assertRaises(ContribuyenteNotFoundError):
            hist_uc.execute("999999999")
        with self.assertRaises(InvalidInputError):
            hist_uc.execute("abc")

        # Guardia parcial: si hay >1000 activos y llega un lote <50%, no marca bajas
        bulk = [
            Contribuyente(
                rnc=str(200000000 + i),
                razon_social=f"EMPRESA {i}",
                actividad_economica="SERVICIOS",
                fecha_inicio_operaciones="01/01/2020",
                estatus="ACTIVO",
                regimen="NORMAL",
            )
            for i in range(1050)
        ]
        bulk_repo = InMemoryRepo(bulk)
        partial_sync = SyncRncCatalogUseCase(
            bulk_repo, InMemorySource([bulk[:5]], etag='"partial"', content_hash="hp")
        )
        partial_sync.execute(force=True)
        self.assertTrue(bulk_repo.data[bulk[-1].rnc].en_padron)


class TestConnectionPoolResilience(unittest.TestCase):
    def test_pool_timeout_raises_database_unavailable(self) -> None:
        cfg = DatabaseSettings(
            "localhost", 5432, "u", "p", "db", pool_max=1, pool_timeout_seconds=0.1
        )
        mgr = PostgresConnectionManager(cfg)

        fake_conn = MagicMock()
        fake_conn.closed = False
        fake_pool = MagicMock()
        fake_pool.getconn.return_value = fake_conn
        mgr._pool = fake_pool

        with mgr.connection():
            with self.assertRaises(DatabaseUnavailableError):
                with mgr.connection():
                    pass

    def test_query_canceled_and_operational_error_translation(self) -> None:
        cfg = DatabaseSettings(
            "localhost", 5432, "u", "p", "db", pool_max=2, pool_timeout_seconds=1.0
        )
        mgr = PostgresConnectionManager(cfg)

        fake_conn = MagicMock()
        fake_conn.closed = False
        fake_pool = MagicMock()
        fake_pool.getconn.return_value = fake_conn
        mgr._pool = fake_pool

        with self.assertRaises(DatabaseTimeoutError):
            with mgr.connection():
                raise QueryCanceled("canceling statement due to statement timeout")
        fake_conn.rollback.assert_called_once()
        fake_pool.putconn.assert_called_with(fake_conn, close=False)

        fake_pool.reset_mock()
        with self.assertRaises(DatabaseUnavailableError):
            with mgr.connection():
                raise OperationalError("connection closed")
        fake_pool.putconn.assert_called_with(fake_conn, close=True)
