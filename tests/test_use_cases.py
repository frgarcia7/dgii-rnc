"""Pruebas unitarias de los casos de uso y reglas de dominio."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.application.use_cases import (
    CheckHealthUseCase,
    GetContribuyenteByRncUseCase,
    InitializeCatalogUseCase,
    SearchContribuyentesUseCase,
    SyncRncCatalogUseCase,
)
from app.core.config import Settings
from app.domain.entities import Contribuyente
from app.domain.exceptions import ContribuyenteNotFoundError, InvalidInputError


class InMemoryRepo:
    """Repositorio en memoria para pruebas aisladas sin PostgreSQL."""

    def __init__(self, initial: list[Contribuyente] | None = None) -> None:
        self.schema_initialized = False
        self.healthy = True
        self.data: dict[str, Contribuyente] = {
            item.rnc: item for item in (initial or [])
        }

    def init_schema(self) -> None:
        self.schema_initialized = True

    def count(self) -> int:
        return len(self.data)

    def get_by_rnc(self, rnc: str) -> Contribuyente | None:
        return self.data.get(rnc)

    def search_by_razon_social(
        self, query: str, limit: int, offset: int
    ) -> list[Contribuyente]:
        q = query.upper()
        matches = [
            item for item in self.data.values() if q in item.razon_social.upper()
        ]
        matches.sort(key=lambda item: item.razon_social)
        return matches[offset : offset + limit]

    def upsert_batch(self, items: list[Contribuyente]) -> int:
        for item in items:
            self.data[item.rnc] = item
        return len(items)

    def is_healthy(self) -> bool:
        return self.healthy


class InMemorySource:
    """Fuente simulada de lotes DGII."""

    def __init__(self, batches: list[list[Contribuyente]]) -> None:
        self._batches = batches

    def stream_batches(self, _batch_size: int):
        for batch in self._batches:
            yield batch


class TestUseCases(unittest.TestCase):
    def setUp(self) -> None:
        self.sample = Contribuyente(
            rnc="130663041",
            razon_social="CONTRATISTAS DE INFRAESTRUCTURAS DE TELEFONOS ELECTRICIDAD Y CABLE S R L",
            actividad_economica="EJECUCIÓN Y MANTENIMIENTO DE INSTALACIONES ELÉCTRICAS Y ELECTRÓNICAS N.C.P.",
            fecha_inicio_operaciones="01/12/2009",
            estatus="SUSPENDIDO",
            regimen="NORMAL",
        )

    def test_get_by_rnc_normalizes_hyphens(self) -> None:
        repo = InMemoryRepo([self.sample])
        uc = GetContribuyenteByRncUseCase(repo)

        found = uc.execute("130-66304-1")
        self.assertEqual(found.rnc, "130663041")
        self.assertEqual(found.estatus, "SUSPENDIDO")

    def test_get_by_rnc_raises_not_found(self) -> None:
        uc = GetContribuyenteByRncUseCase(InMemoryRepo())
        with self.assertRaises(ContribuyenteNotFoundError):
            uc.execute("999999999")

    def test_get_by_rnc_raises_invalid_on_blank(self) -> None:
        uc = GetContribuyenteByRncUseCase(InMemoryRepo())
        with self.assertRaises(InvalidInputError):
            uc.execute("  ---  ")

    def test_search_returns_paginated_matches(self) -> None:
        items = [
            self.sample,
            Contribuyente("101010101", "TELEFONOS DEL CARIBE SRL", "COMERCIO", "01/01/2015", "ACTIVO", "NORMAL"),
        ]
        uc = SearchContribuyentesUseCase(InMemoryRepo(items))

        results = uc.execute("  telefonos ", limit=1, offset=1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].rnc, "101010101")

    def test_search_raises_invalid_on_empty_query(self) -> None:
        uc = SearchContribuyentesUseCase(InMemoryRepo())
        with self.assertRaises(InvalidInputError):
            uc.execute("   ")

    def test_sync_and_initialize_populate_empty_repo(self) -> None:
        repo = InMemoryRepo()
        source = InMemorySource([[self.sample]])
        sync_uc = SyncRncCatalogUseCase(repo, source, batch_size=100)
        init_uc = InitializeCatalogUseCase(repo, sync_uc)

        init_uc.execute()

        self.assertTrue(repo.schema_initialized)
        self.assertEqual(repo.count(), 1)
        self.assertTrue(CheckHealthUseCase(repo).execute())

    def test_settings_validates_cron_bounds(self) -> None:
        with patch.dict(os.environ, {"SYNC_HOUR": "25"}):
            with self.assertRaises(ValueError):
                Settings.from_env()

