"""Endpoints REST de consulta, validación y estadísticas de contribuyentes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.application.use_cases import (
    BatchLookupRncUseCase,
    GetContribuyenteByRncUseCase,
    GetStatsUseCase,
    SearchContribuyentesUseCase,
    ValidateRncUseCase,
)
from app.core.config import Settings
from app.presentation.dependencies import (
    get_batch_use_case,
    get_rnc_use_case,
    get_search_use_case,
    get_settings,
    get_stats_use_case,
    get_validate_use_case,
    require_api_key,
)
from app.presentation.schemas.contribuyente import (
    BatchLookupRequest,
    BatchLookupResponse,
    ContribuyenteResponse,
    ErrorResponse,
    RncValidationResponse,
    SearchResponse,
    StatsResponse,
)

router = APIRouter(
    tags=["Contribuyentes"],
    dependencies=[Depends(require_api_key)],
)


@router.get(
    "/rnc/{rnc}/validar",
    response_model=RncValidationResponse,
    summary="Validar RNC/Cédula y verificar registro",
    description=(
        "Valida el dígito verificador (Módulo 11 para RNC de 9 dígitos, Módulo 10/Luhn "
        "para Cédula de 11 dígitos) y verifica si existe en el padrón local de la DGII."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "Entrada vacía.",
        },
    },
)
def validate_rnc(
    rnc: str = Path(
        ...,
        description="RNC (9 dígitos) o Cédula (11 dígitos), con o sin guiones.",
        examples=["130-66304-1"],
    ),
    use_case: ValidateRncUseCase = Depends(get_validate_use_case),
) -> RncValidationResponse:
    validation, contribuyente = use_case.execute(rnc)
    return RncValidationResponse.from_domain(validation, contribuyente)


@router.post(
    "/rnc/batch",
    response_model=BatchLookupResponse,
    summary="Consultar múltiples RNCs o Cédulas en lote",
    description=(
        "Consulta hasta el límite configurado (`BATCH_MAX_ITEMS`, por defecto 200) de RNCs/Cédulas "
        "en una sola petición SQL. Ideal para validar listas de proveedores en formatos 606/607."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "Lista vacía o excede el límite máximo por lote.",
        },
    },
)
def batch_lookup(
    payload: BatchLookupRequest,
    use_case: BatchLookupRncUseCase = Depends(get_batch_use_case),
) -> BatchLookupResponse:
    batch_result = use_case.execute(payload.rncs)
    return BatchLookupResponse(
        requested=len(payload.rncs),
        found_count=len(batch_result.results),
        results=[
            ContribuyenteResponse.from_domain(item) for item in batch_result.results
        ],
        not_found=batch_result.not_found,
        invalid=batch_result.invalid,
    )


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
    response: Response,
    rnc: str = Path(
        ...,
        description="Número de RNC (9 dígitos) o Cédula (11 dígitos), con o sin guiones.",
        examples=["101010101"],
    ),
    use_case: GetContribuyenteByRncUseCase = Depends(get_rnc_use_case),
    settings: Settings = Depends(get_settings),
) -> ContribuyenteResponse:
    entity = use_case.execute(rnc)
    if settings.api.cache_max_age_seconds > 0:
        response.headers["Cache-Control"] = (
            f"public, max-age={settings.api.cache_max_age_seconds}"
        )
    return ContribuyenteResponse.from_domain(entity)


@router.get(
    "/buscar",
    response_model=SearchResponse,
    summary="Buscar contribuyentes por razón social",
    description=(
        "Búsqueda parcial insensible a mayúsculas y tildes (`unaccent` + `pg_trgm`) "
        "sobre `razon_social`, ordenada por relevancia y con filtros opcionales."
    ),
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": (
                "El parámetro `query` tiene menos de 2 caracteres tras limpiar espacios."
            ),
        },
    },
)
def search_by_name(
    query: str = Query(
        ...,
        min_length=2,
        description=(
            "Texto parcial o completo a buscar dentro de la razón social (mínimo 2 caracteres)."
        ),
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
    estatus: str | None = Query(
        default=None,
        description="Filtrar por estatus exacto (ej. `ACTIVO`, `SUSPENDIDO`, `DADO DE BAJA`).",
        examples=["ACTIVO"],
    ),
    regimen: str | None = Query(
        default=None,
        description="Filtrar por régimen tributario (ej. `NORMAL`, `RST`).",
        examples=["NORMAL"],
    ),
    actividad: str | None = Query(
        default=None,
        description="Filtrar por texto contenido en la actividad económica.",
    ),
    use_case: SearchContribuyentesUseCase = Depends(get_search_use_case),
) -> SearchResponse:
    cleaned_query = " ".join(query.split())
    search_result = use_case.execute(
        raw_query=cleaned_query,
        limit=limit,
        offset=offset,
        estatus=estatus,
        regimen=regimen,
        actividad=actividad,
    )
    results = [ContribuyenteResponse.from_domain(item) for item in search_result.items]
    return SearchResponse(
        query=cleaned_query,
        count=len(results),
        total=search_result.total,
        limit=limit,
        offset=offset,
        has_more=(offset + len(results)) < search_result.total,
        results=results,
    )


@router.get(
    "/stats",
    response_model=StatsResponse,
    summary="Estadísticas y estado de actualización del padrón",
    description=(
        "Retorna el total de contribuyentes almacenados, distribución por estatus/régimen "
        "y los datos de la última sincronización ejecutada."
    ),
)
def get_stats(
    use_case: GetStatsUseCase = Depends(get_stats_use_case),
) -> StatsResponse:
    return StatsResponse.from_domain(use_case.execute())
