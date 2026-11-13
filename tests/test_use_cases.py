"""Pruebas unitarias de los casos de uso y reglas de dominio."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

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
from app.core.config import Settings
from app.domain.entities import (
    Contribuyente,
    format_rnc,
    is_valid_cedula_check_digit,
    is_valid_rnc_check_digit,
    validate_rnc_format,
)
from app.domain.exceptions import ContribuyenteNotFoundError, InvalidInputError
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
                "API_KEY": "secret-key",
            },
        ):
            cfg = Settings.from_env()
            self.assertFalse(cfg.sync.enable_scheduler)
            self.assertEqual(
                cfg.api.cors_origins, ("http://localhost:3000", "https://erp.local")
            )
            self.assertEqual(cfg.api.api_key, "secret-key")
