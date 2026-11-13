# DGII RNC API

API REST en **FastAPI + PostgreSQL 18 + Python 3.15 + Docker** con **Arquitectura Limpia** y documentación **Swagger / OpenAPI** para descargar, almacenar y consultar el padrón del Registro Nacional de Contribuyentes (RNC) de la DGII de República Dominicana.

## Arquitectura Limpia

```text
app/
├── main.py                      # Composition Root, Swagger/OpenAPI y lifespan
├── core/config.py               # Configuración por variables de entorno
├── domain/                      # Entidades puras, excepciones y puertos (Protocols)
├── application/use_cases.py     # Casos de uso (independientes de FastAPI y PostgreSQL)
├── infrastructure/
│   ├── db/                      # Pool psycopg2 (RealDictCursor), pg_trgm y UPSERT execute_values
│   ├── dgii/                    # Descarga HTTP, descompresión en BytesIO e ISO-8859-1
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

## Documentación Swagger / OpenAPI

- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`
- **Esquema OpenAPI JSON:** `http://localhost:8000/openapi.json`

## Endpoints

- `GET /api/rnc/{rnc}`: Consulta exacta por RNC o Cédula (con o sin guiones).
- `GET /api/buscar?query={texto}&limit=50&offset=0`: Búsqueda parcial por `razon_social` (`ILIKE` + `gin_trgm_ops`).
- `POST /api/sync`: Dispara la sincronización manual en segundo plano (`BackgroundTasks`).
- `GET /health`: Verificación de estado de la API y PostgreSQL.
