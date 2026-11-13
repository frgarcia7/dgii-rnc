"""Pruebas de integración contra PostgreSQL real (PG 18 + pg_trgm + unaccent)."""

from __future__ import annotations

import os
import unittest

import pytest

from app.core.config import DatabaseSettings
from app.domain.entities import Contribuyente
from app.infrastructure.db.connection import PostgresConnectionManager
from app.infrastructure.db.postgres_repository import PostgresContribuyenteRepository

RUN_PG_TESTS = os.getenv("RUN_POSTGRES_TESTS", "").lower() in {"1", "true", "yes"}


@pytest.mark.integration
@unittest.skipUnless(
    RUN_PG_TESTS, "Se requiere PostgreSQL activo y RUN_POSTGRES_TESTS=true."
)
class TestPostgresIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cfg = DatabaseSettings(
            host=os.getenv("DB_HOST", "localhost"),
            port=int(os.getenv("DB_PORT", "5432")),
            user=os.getenv("DB_USER", "dgii_user"),
            password=os.getenv("DB_PASSWORD", "dgii_password"),
            name=os.getenv("DB_NAME", "dgii_rnc"),
        )
        cls.conn_manager = PostgresConnectionManager(cfg)
        cls.repo = PostgresContribuyenteRepository(cls.conn_manager)
        cls.repo.init_schema()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.conn_manager.close()

    def setUp(self) -> None:
        with self.conn_manager.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "TRUNCATE TABLE contribuyente_historial, sync_seen_rncs, "
                    "contribuyentes, sync_runs RESTART IDENTITY CASCADE;"
                )

    def test_upsert_distinct_search_unaccent_and_advisory_lock(self) -> None:
        c1 = Contribuyente(
            rnc="130663041",
            razon_social="CLÍNICA DEL CORAZÓN SRL",
            actividad_economica="SALUD",
            fecha_inicio_operaciones="01/12/2009",
            estatus="ACTIVO",
            regimen="NORMAL",
        )
        c2 = Contribuyente(
            rnc="101010101",
            razon_social="FERRETERÍA JOSÉ PÉREZ EIRL",
            actividad_economica="COMERCIO",
            fecha_inicio_operaciones="10/02/2018",
            estatus="SUSPENDIDO",
            regimen="RST",
        )

        processed, changed = self.repo.upsert_batch([c1, c2])
        self.assertEqual((processed, changed), (2, 2))

        # Re-upsert idéntico no debe marcar cambios en BD
        processed_2, changed_2 = self.repo.upsert_batch([c1, c2])
        self.assertEqual((processed_2, changed_2), (2, 0))

        # Búsqueda sin tildes debe encontrar CLÍNICA
        search_res = self.repo.search_by_razon_social("clinica corazon", limit=10, offset=0)
        self.assertEqual(search_res.total, 1)
        self.assertEqual(search_res.items[0].rnc, "130663041")

        # Búsqueda de 2 caracteres usa prefijo indexado
        prefix_res = self.repo.search_by_razon_social("cl", limit=10, offset=0)
        self.assertEqual(prefix_res.total, 1)
        self.assertEqual(prefix_res.items[0].rnc, "130663041")

        # has_records, is_sync_locked y limpieza de corridas huérfanas
        self.assertTrue(self.repo.has_records())
        self.assertFalse(self.repo.is_sync_locked())

        # Filtro por estatus y lote
        batch = self.repo.get_by_rncs(["130663041", "101010101"])
        self.assertEqual(len(batch), 2)

        with self.repo.sync_lock() as acquired:
            self.assertTrue(acquired)
            self.assertTrue(self.repo.is_sync_locked())

        self.assertFalse(self.repo.is_sync_locked())

        stale_id = self.repo.record_sync_start("https://dgii.gov.do/stale.zip")
        assert stale_id is not None
        self.assertEqual(self.repo.cleanup_stale_sync_runs(), 1)
        latest_stale = self.repo.get_latest_sync()
        assert latest_stale is not None
        self.assertEqual(latest_stale.status, "interrupted")

        self.repo.analyze_catalog()

        run_id = self.repo.record_sync_start("https://dgii.gov.do/test.zip")
        self.repo.record_sync_finish(
            run_id=run_id,
            status="completed",
            processed=2,
            changed=2,
            elapsed_seconds=0.5,
            etag='"test"',
            content_hash="abc123",
        )
        stats = self.repo.get_stats(is_syncing=False)
        self.assertEqual(stats.total_contribuyentes, 2)
        self.assertIsNotNone(stats.last_sync)

    def test_fts_fuzzy_numeric_prefix_and_sync_history(self) -> None:
        ferr = Contribuyente(
            rnc="131999888",
            razon_social="FERRETERÍA HERMANOS OCHOA SRL",
            actividad_economica="VENTA DE MATERIALES ELÉCTRICOS",
            fecha_inicio_operaciones="01/03/2015",
            estatus="ACTIVO",
            regimen="NORMAL",
        )
        clinica = Contribuyente(
            rnc="130663041",
            razon_social="CLÍNICA DEL CORAZÓN SRL",
            actividad_economica="SALUD",
            fecha_inicio_operaciones="01/12/2009",
            estatus="ACTIVO",
            regimen="NORMAL",
        )

        self.repo.begin_sync_tracking()
        self.repo.upsert_batch([ferr, clinica], track_seen=True, record_history=False)
        self.repo.finalize_sync_tracking(sync_run_id=None, processed_count=2, record_history=False)

        # Multi-palabra FTS español (plural + palabras no contiguas)
        multi = self.repo.search_by_razon_social("ferreterias ochoa", limit=10, offset=0)
        self.assertEqual(multi.total, 1)
        self.assertEqual(multi.items[0].rnc, "131999888")

        # Fallback fuzzy word_similarity con error tipográfico
        fuzzy = self.repo.search_by_razon_social("clinica corason", limit=10, offset=0)
        self.assertEqual(fuzzy.total, 1)
        self.assertEqual(fuzzy.items[0].rnc, "130663041")

        # Prefijo numérico RNC y búsqueda solo por actividad
        rnc_pref = self.repo.search_by_razon_social("13199", limit=10, offset=0)
        self.assertEqual(rnc_pref.items[0].rnc, "131999888")

        act_only = self.repo.search_by_razon_social(
            None, limit=10, offset=0, actividad="materiales electricos"
        )
        self.assertEqual(act_only.total, 1)
        self.assertEqual(act_only.items[0].rnc, "131999888")

        # Ciclo de cambios entre syncs: ACTUALIZACION + BAJA_PADRON + REINGRESO
        run_2 = self.repo.record_sync_start("dgii")
        self.repo.begin_sync_tracking()
        ferr_updated = Contribuyente(
            rnc=ferr.rnc,
            razon_social=ferr.razon_social,
            actividad_economica=ferr.actividad_economica,
            fecha_inicio_operaciones=ferr.fecha_inicio_operaciones,
            estatus="SUSPENDIDO",
            regimen=ferr.regimen,
        )
        self.repo.upsert_batch(
            [ferr_updated], sync_run_id=run_2, record_history=True, track_seen=True
        )
        bajas = self.repo.finalize_sync_tracking(
            sync_run_id=run_2, processed_count=1, record_history=True
        )
        self.assertEqual(bajas, 1)

        ferr_hist = self.repo.get_history_by_rnc("131999888")
        self.assertEqual(len(ferr_hist), 1)
        self.assertEqual(ferr_hist[0].cambio_tipo, "ACTUALIZACION")
        self.assertEqual(ferr_hist[0].estatus_nuevo, "SUSPENDIDO")

        dropped = self.repo.get_by_rnc("130663041")
        assert dropped is not None
        self.assertFalse(dropped.en_padron)
        clinica_hist = self.repo.get_history_by_rnc("130663041")
        self.assertEqual(clinica_hist[0].cambio_tipo, "BAJA_PADRON")

        # Reingreso
        run_3 = self.repo.record_sync_start("dgii")
        self.repo.begin_sync_tracking()
        self.repo.upsert_batch(
            [ferr_updated, clinica], sync_run_id=run_3, record_history=True, track_seen=True
        )
        self.repo.finalize_sync_tracking(
            sync_run_id=run_3, processed_count=2, record_history=True
        )
        clinica_reentered = self.repo.get_by_rnc("130663041")
        assert clinica_reentered is not None
        self.assertTrue(clinica_reentered.en_padron)
        clinica_hist_2 = self.repo.get_history_by_rnc("130663041")
        self.assertEqual(clinica_hist_2[0].cambio_tipo, "REINGRESO")

