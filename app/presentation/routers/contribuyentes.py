"""Endpoints REST de consulta, validación y estadísticas de contribuyentes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.application.use_cases import (
    BatchLookupRncUseCase,
    GetContribuyenteByRncUseCase,
    GetContribuyenteHistoryUseCase,
    GetStatsUseCase,
    SearchContribuyentesUseCase,
    ValidateRncUseCase,
)
from app.core.config import Settings
from app.domain.entities import format_rnc
from app.presentation.dependencies import (
    get_batch_use_case,
    get_history_use_case,
    get_rnc_use_case,
    get_search_use_case,
    get_settings,
    get_stats_use_case,
    get_validate_use_case,
    rate_limit_batch,
    rate_limit_lookup,
    rate_limit_search,
)
from app.presentation.schemas.contribuyente import (
    BatchLookupRequest,
    BatchLookupResponse,
    ContribuyenteCambioResponse,
    ContribuyenteHistoryResponse,
    ContribuyenteResponse,
    ErrorResponse,
    RncValidationResponse,
    SearchResponse,
    StatsResponse,
)

router = APIRouter(tags=["Contribuyentes"])

_COMMON_RATE_AND_DB_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_429_TOO_MANY_REQUESTS: {
        "model": ErrorResponse,
        "description": "Límite de consultas por minuto excedido.",
    },
    status.HTTP_503_SERVICE_UNAVAILABLE: {
        "model": ErrorResponse,
        "description": "Base de datos temporalmente saturada o no disponible.",
    },
}


def _apply_cache_headers(
    response: Response, settings: Settings, max_age_seconds: int | None = None
) -> None:
    ttl = (
        settings.api.cache_max_age_seconds
        if max_age_seconds is None
        else max_age_seconds
    )
    if ttl > 0:
        response.headers["Cache-Control"] = f"public, max-age={ttl}"


@router.get(
    "/rnc/{rnc}/validar",
    response_model=RncValidationResponse,
    summary="Validar RNC/Cédula y verificar registro",
    description=(
        "Valida el dígito verificador (Módulo 11 para RNC de 9 dígitos, Módulo 10/Luhn "
        "para Cédula de 11 dígitos) y verifica si existe en el padrón local de la DGII."
    ),
    dependencies=[Depends(rate_limit_lookup)],
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "Entrada vacía.",
        },
        **_COMMON_RATE_AND_DB_RESPONSES,
    },
)
def validate_rnc(
    response: Response,
    rnc: str = Path(
        ...,
        description="RNC (9 dígitos) o Cédula (11 dígitos), con o sin guiones.",
        examples=["130-66304-1"],
    ),
    use_case: ValidateRncUseCase = Depends(get_validate_use_case),
    settings: Settings = Depends(get_settings),
) -> RncValidationResponse:
    validation, contribuyente = use_case.execute(rnc)
    _apply_cache_headers(response, settings)
    return RncValidationResponse.from_domain(validation, contribuyente)


@router.post(
    "/rnc/batch",
    response_model=BatchLookupResponse,
    summary="Consultar múltiples RNCs o Cédulas en lote",
    description=(
        "Consulta hasta el límite configurado (`BATCH_MAX_ITEMS`, por defecto 200) de RNCs/Cédulas "
        "en una sola petición SQL. Ideal para validar listas de proveedores en formatos 606/607."
    ),
    dependencies=[Depends(rate_limit_batch)],
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "Lista vacía o excede el límite máximo por lote.",
        },
        **_COMMON_RATE_AND_DB_RESPONSES,
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
    "/rnc/{rnc}/historial",
    response_model=ContribuyenteHistoryResponse,
    summary="Consultar historial de cambios de un RNC o Cédula",
    description=(
        "Retorna el estado actual del contribuyente (incluyendo si sigue en el padrón vigente) "
        "y los últimos cambios detectados entre sincronizaciones (`ALTA`, `ACTUALIZACION`, "
        "`BAJA_PADRON`, `REINGRESO`)."
    ),
    dependencies=[Depends(rate_limit_lookup)],
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "RNC inválido o vacío.",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Sin registros ni historial para ese RNC/Cédula.",
        },
        **_COMMON_RATE_AND_DB_RESPONSES,
    },
)
def get_rnc_history(
    response: Response,
    rnc: str = Path(
        ...,
        description="Número de RNC (9 dígitos) o Cédula (11 dígitos), con o sin guiones.",
        examples=["130-66304-1"],
    ),
    limit: int = Query(
        20,
        ge=1,
        le=100,
        description="Cantidad máxima de eventos históricos recientes a retornar.",
    ),
    use_case: GetContribuyenteHistoryUseCase = Depends(get_history_use_case),
    settings: Settings = Depends(get_settings),
) -> ContribuyenteHistoryResponse:
    contribuyente, history = use_case.execute(rnc, limit=limit)
    resolved_rnc = contribuyente.rnc if contribuyente is not None else history[0].rnc
    en_padron = contribuyente.en_padron if contribuyente is not None else False
    _apply_cache_headers(response, settings)
    return ContribuyenteHistoryResponse(
        rnc=resolved_rnc,
        rnc_formateado=format_rnc(resolved_rnc),
        en_padron=en_padron,
        contribuyente=(
            ContribuyenteResponse.from_domain(contribuyente)
            if contribuyente is not None
            else None
        ),
        total_cambios=len(history),
        cambios=[ContribuyenteCambioResponse.from_domain(item) for item in history],
    )


@router.get(
    "/rnc/{rnc}",
    response_model=ContribuyenteResponse,
    summary="Consultar contribuyente por RNC o Cédula",
    description=(
        "Retorna los datos oficiales de un contribuyente dado su RNC o Cédula. "
        "Acepta valores con o sin guiones (por ejemplo, `101010101` o `1-01-01010-1`)."
    ),
    dependencies=[Depends(rate_limit_lookup)],
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": "RNC inválido o vacío.",
        },
        status.HTTP_404_NOT_FOUND: {
            "model": ErrorResponse,
            "description": "Contribuyente no encontrado en el padrón.",
        },
        **_COMMON_RATE_AND_DB_RESPONSES,
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
    _apply_cache_headers(response, settings)
    return ContribuyenteResponse.from_domain(entity)


@router.get(
    "/buscar",
    response_model=SearchResponse,
    summary="Buscar contribuyentes por razón social, RNC parcial o actividad",
    description=(
        "Búsqueda tolerante a mayúsculas, tildes, múltiples palabras, errores tipográficos "
        "leves (`unaccent` + FTS español + `pg_trgm`) y prefijos numéricos de RNC. Permite "
        "consultar solo por `actividad` cuando no se especifica `query`."
    ),
    dependencies=[Depends(rate_limit_search)],
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "model": ErrorResponse,
            "description": (
                "Debe indicar `query` o `actividad` con al menos 2 caracteres útiles."
            ),
        },
        status.HTTP_504_GATEWAY_TIMEOUT: {
            "model": ErrorResponse,
            "description": "La búsqueda superó el tiempo máximo permitido en base de datos.",
        },
        **_COMMON_RATE_AND_DB_RESPONSES,
    },
)
def search_by_name(
    query: str | None = Query(
        default=None,
        min_length=2,
        description=(
            "Razón social o prefijo numérico de RNC/Cédula (opcional si envía `actividad`)."
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
        le=2000,
        description="Desplazamiento para paginación (máximo 2000).",
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
    cleaned_query = " ".join(query.split()) if query is not None else None
    search_result = use_case.execute(
        raw_query=query,
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
    dependencies=[Depends(rate_limit_lookup)],
    responses=_COMMON_RATE_AND_DB_RESPONSES,
)
def get_stats(
    response: Response,
    use_case: GetStatsUseCase = Depends(get_stats_use_case),
    settings: Settings = Depends(get_settings),
) -> StatsResponse:
    _apply_cache_headers(
        response,
        settings,
        max_age_seconds=min(60, settings.api.cache_max_age_seconds),
    )
    return StatsResponse.from_domain(use_case.execute())
