"""DTOs Pydantic v2 enriquecidos con ejemplos y descripciones para Swagger UI."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.domain.entities import Contribuyente


class ContribuyenteResponse(BaseModel):
    """Datos públicos de un contribuyente registrado en la DGII."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "rnc": "130663041",
                "razon_social": "CONTRATISTAS DE INFRAESTRUCTURAS DE TELEFONOS ELECTRICIDAD Y CABLE S R L",
                "actividad_economica": "EJECUCIÓN Y MANTENIMIENTO DE INSTALACIONES ELÉCTRICAS Y ELECTRÓNICAS N.C.P. (INCL. LA INSTAL. DE ANTENAS, PARARRAYOS, SISTEMAS DE",
                "fecha_inicio_operaciones": "01/12/2009",
                "estatus": "SUSPENDIDO",
                "regimen": "NORMAL",
                "updated_at": "2026-11-13T00:55:00Z",
            }
        }
    )

    rnc: str = Field(..., description="RNC (9 dígitos) o Cédula (11 dígitos) sin guiones.")
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
        description="Fecha y hora de la última actualización del registro.",
    )

    @classmethod
    def from_domain(cls, entity: Contribuyente) -> ContribuyenteResponse:
        """Convierte una entidad de dominio al DTO HTTP."""
        return cls(
            rnc=entity.rnc,
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
    limit: int = Field(..., description="Límite máximo solicitado por página.")
    offset: int = Field(..., description="Desplazamiento aplicado para paginación.")
    results: list[ContribuyenteResponse] = Field(
        default_factory=list,
        description="Lista de contribuyentes coincidentes.",
    )


class SyncResponse(BaseModel):
    """Respuesta al solicitar una sincronización manual del padrón."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "accepted",
                "message": "Sincronización iniciada en segundo plano.",
            }
        }
    )

    status: str = Field(..., description="Estado de la solicitud (`accepted` o `running`).")
    message: str = Field(..., description="Detalle legible de la operación.")


class HealthResponse(BaseModel):
    """Estado de salud del servicio y la base de datos."""

    model_config = ConfigDict(
        json_schema_extra={"example": {"status": "ok", "database": "connected"}}
    )

    status: str = Field(..., description="Estado general del servicio.")
    database: str = Field(..., description="Estado de conexión con PostgreSQL.")


class ErrorResponse(BaseModel):
    """Esquema estándar de error HTTP para respuestas 4xx/5xx en Swagger."""

    detail: str = Field(..., description="Descripción del error ocurrido.")
