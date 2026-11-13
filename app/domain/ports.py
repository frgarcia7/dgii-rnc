"""Puertos (interfaces) de dominio siguiendo inversión de dependencias."""

from __future__ import annotations

from typing import Iterator, Protocol

from app.domain.entities import Contribuyente


class ContribuyenteRepository(Protocol):
    """Contrato de persistencia y consulta de contribuyentes."""

    def init_schema(self) -> None:
        """Inicializa extensiones, tablas e índices necesarios."""
        ...

    def count(self) -> int:
        """Retorna el total de contribuyentes almacenados."""
        ...

    def get_by_rnc(self, rnc: str) -> Contribuyente | None:
        """Obtiene un contribuyente por su RNC exacto."""
        ...

    def search_by_razon_social(
        self, query: str, limit: int, offset: int
    ) -> list[Contribuyente]:
        """Busca contribuyentes por coincidencia parcial en razón social."""
        ...

    def upsert_batch(self, items: list[Contribuyente]) -> int:
        """Inserta o actualiza un lote de contribuyentes."""
        ...

    def is_healthy(self) -> bool:
        """Verifica la disponibilidad del repositorio."""
        ...


class DgiiSourcePort(Protocol):
    """Contrato para obtener lotes de contribuyentes desde la fuente oficial DGII."""

    def stream_batches(self, batch_size: int) -> Iterator[list[Contribuyente]]:
        """Descarga y entrega lotes deduplicados de contribuyentes."""
        ...

