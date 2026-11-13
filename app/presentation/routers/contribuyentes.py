"""Endpoints REST de consulta de contribuyentes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, status

from app.application.use_cases import (
    GetContribuyenteByRncUseCase,
    SearchContribuyentesUseCase,
)
from app.presentation.dependencies import get_rnc_use_case, get_search_use_case
from app.presentation.schemas.contribuyente import (
    ContribuyenteResponse,
    ErrorResponse,
    SearchResponse,
)

router = APIRouter(prefix="/api", tags=["Contribuyentes"])


@router.get(
    "/rnc/{rnc}",
    response_model=ContribuyenteResponse,
    summary="Consultar contribuyente por RNC o Cédula",
    description=(
        "Retorna los datos oficiales de un contribuyente dado su RNC o Cédula. "
        "Acepta valores con o sin guiones (por ejemplo, `101010101` o `1-01-01010-1`)."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "RNC inválido o vacío.",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Contribuyente no encontrado en el padrón.",
        },
    },
)
def get_by_rnc(
    rnc: str = Path(
        ...,
        description="Número de RNC (9 dígitos) o Cédula (11 dígitos), con o sin guiones.",
        examples=["101010101"],
    ),
    use_case: GetContribuyenteByRncUseCase = Depends(get_rnc_use_case),
) -> ContribuyenteResponse:
    entity = use_case.execute(rnc)
    return ContribuyenteResponse.from_domain(entity)


@router.get(
    "/buscar",
    response_model=SearchResponse,
    summary="Buscar contribuyentes por razón social",
    description=(
        "Realiza una búsqueda parcial insensible a mayúsculas (`ILIKE %query%`) "
        "sobre `razon_social`, acelerada mediante el índice GIN `pg_trgm`."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "El parámetro `query` está vacío tras limpiar espacios.",
        },
    },
)
def search_by_name(
    query: str = Query(
        ...,
        min_length=1,
        description="Texto parcial o completo a buscar dentro de la razón social.",
        examples=["BANCO POPULAR"],
    ),
    limit: int = Query(
        50,
        ge=1,
        le=200,
        description="Cantidad máxima de registros por página.",
    ),
    offset: int = Query(
        0,
        ge=0,
        description="Desplazamiento para paginación.",
    ),
    use_case: SearchContribuyentesUseCase = Depends(get_search_use_case),
) -> SearchResponse:
    cleaned_query = query.strip()
    entities = use_case.execute(raw_query=cleaned_query, limit=limit, offset=offset)
    results = [ContribuyenteResponse.from_domain(item) for item in entities]
    return SearchResponse(
        query=cleaned_query,
        count=len(results),
        limit=limit,
        offset=offset,
        results=results,
    )

