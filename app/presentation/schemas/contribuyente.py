"""DTOs Pydantic v2 enriquecidos con ejemplos y descripciones para Swagger UI."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.entities import (
    CatalogStats,
    Contribuyente,
    RncValidation,
    SyncRun,
    detect_rnc_type,
    format_rnc,
)


class ContribuyenteResponse(BaseModel):
    """Datos públicos de un contribuyente registrado en la DGII."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "rnc": "130663041",
                "rnc_formateado": "130-66304-1",
                "tipo": "RNC",
                "razon_social": (
                    "CONTRATISTAS DE INFRAESTRUCTURAS DE TELEFONOS ELECTRICIDAD Y CABLE S R L"
                ),
                "actividad_economica": (
                    "EJECUCIÓN Y MANTENIMIENTO DE INSTALACIONES ELÉCTRICAS Y ELECTRÓNICAS N.C.P."
                ),
                "fecha_inicio_operaciones": "01/12/2009",
                "estatus": "SUSPENDIDO",
                "regimen": "NORMAL",
                "updated_at": "2026-11-13T00:55:00Z",
            }
        }
    )

    rnc: str = Field(..., description="RNC (9 dígitos) o Cédula (11 dígitos) sin guiones.")
    rnc_formateado: str = Field(
        ..., description="RNC o Cédula formateado con guiones (XXX-XXXXX-X / XXX-XXXXXXX-X)."
    )
    tipo: str = Field(
        ...,
        description="Tipo de documento detectado (`RNC`, `CEDULA` o `DESCONOCIDO`).",
    )
    razon_social: str = Field(..., description="Razón social registrada en la DGII.")
    actividad_economica: str = Field(..., description="Actividad económica principal.")
    fecha_inicio_operaciones: str = Field(
        ..., description="Fecha de inicio de operaciones registrada en la DGII."
    )
    estatus: str = Field(
        ..., description="Estado en el padrón (ACTIVO, SUSPENDIDO, DADO DE BAJA, etc.)."
    )
    regimen: str = Field(..., description="Régimen de pago tributario (NORMAL, RST, etc.).")
    updated_at: datetime | None = Field(
        default=None,
        description="Fecha y hora del último cambio registrado para este contribuyente.",
    )

    @classmethod
    def from_domain(cls, entity: Contribuyente) -> ContribuyenteResponse:
        """Convierte una entidad de dominio al DTO HTTP."""
        return cls(
            rnc=entity.rnc,
            rnc_formateado=format_rnc(entity.rnc),
            tipo=detect_rnc_type(entity.rnc),
            razon_social=entity.razon_social,
            actividad_economica=entity.actividad_economica,
            fecha_inicio_operaciones=entity.fecha_inicio_operaciones,
            estatus=entity.estatus,
            regimen=entity.regimen,
            updated_at=entity.updated_at,
        )


class SearchResponse(BaseModel):
    """Resultado paginado de la búsqueda por razón social."""

    query: str = Field(..., description="Texto buscado en el campo razón social.")
    count: int = Field(..., description="Cantidad de resultados devueltos en esta página.")
    total: int = Field(..., description="Total de contribuyentes coincidentes con la búsqueda.")
    limit: int = Field(..., description="Límite máximo solicitado por página.")
    offset: int = Field(..., description="Desplazamiento aplicado para paginación.")
    has_more: bool = Field(
        ..., description="Indica si existen más resultados en páginas siguientes."
    )
    results: list[ContribuyenteResponse] = Field(
        default_factory=list,
        description="Lista de contribuyentes coincidentes.",
    )


class RncValidationResponse(BaseModel):
    """Validación matemática (dígito verificador) y verificación en el padrón DGII."""

    rnc: str = Field(..., description="Valor normalizado sin espacios ni guiones.")
    rnc_formateado: str = Field(..., description="Valor formateado con guiones si aplica.")
    tipo: str = Field(..., description="`RNC`, `CEDULA` o `DESCONOCIDO`.")
    formato_valido: bool = Field(..., description="Verdadero si tiene 9 u 11 dígitos numéricos.")
    digito_verificador_valido: bool = Field(
        ..., description="Resultado de la validación Módulo 11 (RNC) o Módulo 10 (Cédula)."
    )
    registrado: bool = Field(
        ..., description="Verdadero si el RNC/Cédula existe en la base de datos local."
    )
    contribuyente: ContribuyenteResponse | None = Field(
        default=None,
        description="Datos del contribuyente si está registrado en el padrón.",
    )

    @classmethod
    def from_domain(
        cls, validation: RncValidation, contribuyente: Contribuyente | None
    ) -> RncValidationResponse:
        return cls(
            rnc=validation.rnc,
            rnc_formateado=validation.rnc_formateado,
            tipo=validation.tipo,
            formato_valido=validation.formato_valido,
            digito_verificador_valido=validation.digito_verificador_valido,
            registrado=contribuyente is not None,
            contribuyente=(
                ContribuyenteResponse.from_domain(contribuyente)
                if contribuyente is not None
                else None
            ),
        )


class BatchLookupRequest(BaseModel):
    """Solicitud de consulta múltiple por lista de RNCs o Cédulas."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {"rncs": ["130-66304-1", "101010101", "999999999"]}
        }
    )

    rncs: list[str] = Field(
        ...,
        min_length=1,
        description="Lista de RNCs o Cédulas (con o sin guiones) a consultar.",
    )


class BatchLookupResponse(BaseModel):
    """Resultado de una consulta múltiple para reportes 606/607/608 u otros lotes."""

    requested: int = Field(..., description="Cantidad de elementos enviados en la solicitud.")
    found_count: int = Field(..., description="Cantidad de contribuyentes encontrados.")
    results: list[ContribuyenteResponse] = Field(
        default_factory=list, description="Contribuyentes encontrados."
    )
    not_found: list[str] = Field(
        default_factory=list,
        description="RNCs/Cédulas válidos no presentes en el padrón local.",
    )
    invalid: list[str] = Field(
        default_factory=list,
        description="Entradas con formato inválido que no pudieron consultarse.",
    )


class SyncRunResponse(BaseModel):
    """Detalle de una ejecución de sincronización del padrón."""

    id: int | None = None
    status: str
    source: str
    processed: int
    changed: int
    elapsed_seconds: float
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None

    @classmethod
    def from_domain(cls, run: SyncRun, sanitize_internal: bool = False) -> SyncRunResponse:
        source = run.source
        error_message = run.error_message
        if sanitize_internal:
            if source.startswith("file://"):
                source = "file://<local>"
            if error_message:
                error_message = (
                    "Error durante la sincronización "
                    "(consulta los logs del servidor o autentícate para ver el detalle)."
                )
        return cls(
            id=run.id,
            status=run.status,
            source=source,
            processed=run.processed,
            changed=run.changed,
            elapsed_seconds=run.elapsed_seconds,
            error_message=error_message,
            started_at=run.started_at,
            finished_at=run.finished_at,
        )


class SyncResponse(BaseModel):
    """Respuesta al solicitar o consultar una sincronización del padrón."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "accepted",
                "message": "Sincronización iniciada en segundo plano.",
                "run": None,
            }
        }
    )

    status: str = Field(..., description="Estado de la operación (`accepted`, `running`, etc.).")
    message: str = Field(..., description="Detalle legible de la operación.")
    run: SyncRunResponse | None = Field(
        default=None, description="Última ejecución registrada si está disponible."
    )


class StatsResponse(BaseModel):
    """Estadísticas globales del padrón almacenado."""

    total_contribuyentes: int
    is_syncing: bool
    by_estatus: dict[str, int]
    by_regimen: dict[str, int]
    last_sync: SyncRunResponse | None = None

    @classmethod
    def from_domain(cls, stats: CatalogStats) -> StatsResponse:
        return cls(
            total_contribuyentes=stats.total_contribuyentes,
            is_syncing=stats.is_syncing,
            by_estatus=stats.by_estatus,
            by_regimen=stats.by_regimen,
            last_sync=(
                SyncRunResponse.from_domain(stats.last_sync)
                if stats.last_sync is not None
                else None
            ),
        )


class HealthResponse(BaseModel):
    """Estado de salud del servicio, la base de datos y la carga inicial."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ok",
                "database": "connected",
                "catalog_ready": True,
                "initializing": False,
                "total_contribuyentes": 750000,
            }
        }
    )

    status: str = Field(..., description="Estado general del servicio.")
    database: str = Field(..., description="Estado de conexión con PostgreSQL.")
    catalog_ready: bool = Field(
        ..., description="Verdadero cuando la base de datos ya contiene registros."
    )
    initializing: bool = Field(
        ..., description="Verdadero mientras corre la carga inicial en segundo plano."
    )
    total_contribuyentes: int = Field(
        ..., description="Total de registros actualmente disponibles en el catálogo."
    )
    batch_max_items: int = Field(
        default=200,
        description="Límite máximo de RNCs/Cédulas aceptados por llamada en `/rnc/batch`.",
    )


class ErrorResponse(BaseModel):
    """Esquema estándar de error HTTP para respuestas 4xx/5xx en Swagger."""

    detail: str = Field(..., description="Descripción del error ocurrido.")
