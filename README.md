# DGII RNC API

![Python](https://img.shields.io/badge/Python-3.15-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.162+-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

API REST en **FastAPI + PostgreSQL 18 + Docker** construida con **Arquitectura Limpia** para descargar, almacenar e indexar el padrón oficial del Registro Nacional de Contribuyentes (RNC) de la **DGII** (República Dominicana).

## Características principales

- **Fuente oficial completa (CSV):** Descarga `RNC_CONTRIBUYENTES.zip` en memoria (`io.BytesIO`), detecta `ISO-8859-1` / `UTF-8`, parsea el CSV oficial con textos completos (sin truncamientos) y mantiene soporte de respaldo para `DGII_RNC.zip` (`.txt` delimitado por `|`).
- **Carga masiva ultrarrápida:** Inserción/actualización por lotes deduplicados mediante `psycopg2.extras.execute_values` (`ON CONFLICT (rnc) DO UPDATE`), sin ORMs pesados.
- **Búsquedas instantáneas:** Habilita la extensión `pg_trgm` y un índice `GIN (razon_social gin_trgm_ops)` para acelerar consultas `ILIKE '%texto%'` sobre millones de registros.
- **Sincronización automática:** Si la tabla está vacía al arrancar, ejecuta la sincronización inicial de inmediato y programa actualizaciones diarias con `APScheduler`.
- **Swagger / OpenAPI:** Documentación interactiva lista en `/docs`, `/swagger` y `/redoc`.

## Arquitectura Limpia

```text
app/
├── main.py                      # Composition Root, Swagger/OpenAPI y lifespan
├── core/config.py               # Configuración por variables de entorno
├── domain/                      # Entidades puras, excepciones y puertos (Protocols)
├── application/use_cases.py     # Casos de uso (independientes de FastAPI y PostgreSQL)
├── infrastructure/
│   ├── db/                      # Pool psycopg2 (RealDictCursor), pg_trgm y UPSERT execute_values
│   ├── dgii/                    # Descarga HTTP, parser CSV/TXT en memoria y detección de encoding
│   └── scheduler/               # Cronjob diario con APScheduler
└── presentation/
    ├── routers/                 # Endpoints REST (/api/rnc, /api/buscar, /api/sync, /health)
    ├── schemas/                 # DTOs Pydantic v2 con ejemplos y descripciones OpenAPI
    ├── dependencies.py          # Inyección de casos de uso con Depends
    └── exception_handlers.py    # Mapeo de excepciones de dominio a códigos HTTP
```

## Inicio rápido

```bash
docker-compose up -d --build
```

Opcionalmente, copia `.env.example` a `.env` para personalizar credenciales y horario del cronjob:

```bash
cp .env.example .env
docker-compose up -d --build
```

> **Nota de seguridad:** Cambia `DB_USER` y `DB_PASSWORD` en tu archivo `.env` antes de desplegar en producción.

## Variables de entorno

| Variable | Descripción | Valor por defecto |
| --- | --- | --- |
| `DB_USER` | Usuario de PostgreSQL | `dgii_user` |
| `DB_PASSWORD` | Contraseña de PostgreSQL | `dgii_password` |
| `DB_NAME` | Nombre de la base de datos | `dgii_rnc` |
| `DGII_RNC_URL` | URL del archivo ZIP oficial de la DGII | `https://dgii.gov.do/app/WebApps/Consultas/RNC/RNC_CONTRIBUYENTES.zip` |
| `SYNC_HOUR` | Hora diaria de sincronización (`0-23`) | `3` |
| `SYNC_MINUTE` | Minuto diario de sincronización (`0-59`) | `0` |
| `TZ` | Zona horaria del contenedor API | `America/Santo_Domingo` |

## Documentación Swagger / OpenAPI

- **Swagger UI:** `http://localhost:8000/docs` (también accesible vía `/swagger` y `/`)
- **ReDoc:** `http://localhost:8000/redoc`
- **Esquema OpenAPI JSON:** `http://localhost:8000/openapi.json`

## Endpoints y ejemplos

### `GET /api/rnc/{rnc}`
Consulta exacta por RNC o Cédula (acepta valores con o sin guiones).

```bash
curl http://localhost:8000/api/rnc/130-66304-1
```

```json
{
  "rnc": "130663041",
  "razon_social": "CONTRATISTAS DE INFRAESTRUCTURAS DE TELEFONOS ELECTRICIDAD Y CABLE S R L",
  "actividad_economica": "EJECUCIÓN Y MANTENIMIENTO DE INSTALACIONES ELÉCTRICAS Y ELECTRÓNICAS N.C.P. (INCL. LA INSTAL. DE ANTENAS, PARARRAYOS, SISTEMAS DE",
  "fecha_inicio_operaciones": "01/12/2009",
  "estatus": "SUSPENDIDO",
  "regimen": "NORMAL",
  "updated_at": "2026-11-13T00:55:00Z"
}
```

### `GET /api/buscar?query={texto}`
Búsqueda parcial por `razon_social` (`ILIKE %texto%` con `pg_trgm`) y paginación.

```bash
curl "http://localhost:8000/api/buscar?query=telefonos&limit=10&offset=0"
```

### `POST /api/sync`
Dispara manualmente la descarga y actualización del padrón en segundo plano (`BackgroundTasks`).

```bash
curl -X POST http://localhost:8000/api/sync
```

### `GET /health`
Verifica que la API y PostgreSQL estén activos.

```bash
curl http://localhost:8000/health
```

## Pruebas unitarias

```bash
python3 -m unittest discover -s tests -v
```

## Licencia

Distribuido bajo la licencia [MIT](LICENSE).
