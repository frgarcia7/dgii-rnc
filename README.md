# DGII RNC API

![Python](https://img.shields.io/badge/Python-3.15-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-0.162+-009688?logo=fastapi&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-18-4169E1?logo=postgresql&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white)
![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)

API REST y buscador web **open-source y self-hosted** en **FastAPI + PostgreSQL 18 + Docker** para que cualquier empresa, ERP, sistema de facturación (NCF / e-CF) o equipo contable en República Dominicana pueda desplegar localmente el padrón oficial del Registro Nacional de Contribuyentes (RNC) de la **DGII**.

> **Aviso:** Este proyecto es una herramienta comunitaria independiente que consume el padrón público oficial publicado por la Dirección General de Impuestos Internos (DGII). No es un servicio oficial de la DGII.

## Características principales

- **Listo para toda la empresa:** Incluye interfaz web integrada en `/` (búsqueda individual y validación en lote para reportes 606/607 con exportación a CSV) y documentación API en `/docs` y `/redoc`.
- **Validación dominicana completa:** Verifica dígito verificador con **Módulo 11 (RNC de 9 dígitos)** y **Módulo 10 / Luhn (Cédula de 11 dígitos)** además de consultar el padrón local.
- **Consultas en lote:** `POST /api/v1/rnc/batch` resuelve listas de hasta 200 RNCs/Cédulas en una sola consulta SQL.
- **Búsqueda inteligente en español:** Combina Full-Text Search en español + `pg_trgm` (`word_similarity`) + `unaccent` (`immutable_unaccent`) para tolerar tildes, múltiples palabras en distinto orden (ej. `"ferreteria ochoa"`), prefijos numéricos de RNC y errores tipográficos leves, con filtros por `estatus`, `regimen` y `actividad` (incluso sin `query`).
- **Trazabilidad del padrón:** Registra `ALTA`, `ACTUALIZACION`, `BAJA_PADRON` y `REINGRESO` entre sincronizaciones (`GET /api/v1/rnc/{rnc}/historial`) y expone el indicador `en_padron`.
- **Sincronización eficiente y resiliente:**
  - Parser tolerante a UTF-8/Windows-1252 y delimitadores `,`, `;` o `|` tanto en ZIP como en archivos planos.
  - Descarga en streaming (`SpooledTemporaryFile`, sin cargar el archivo completo en RAM) con reintentos automáticos.
  - Caché por `ETag` y `SHA-256` para omitir descargas/escrituras cuando el padrón no ha cambiado.
  - `UPSERT` inteligente (`IS DISTINCT FROM`) que solo actualiza `updated_at` cuando un contribuyente cambia realmente.
  - Arranque **no bloqueante**: la API responde `/health` y `/docs` de inmediato mientras realiza la primera carga en segundo plano.
  - Bloqueo distribuido vía **PostgreSQL advisory lock** para evitar ejecuciones simultáneas entre múltiples workers.
  - Soporte offline mediante `DGII_LOCAL_ZIP_PATH` o CLI (`python -m app.cli sync --file ...`).

## Arquitectura Limpia

```text
app/
├── main.py                      # Composition Root, CORS, Swagger/OpenAPI y lifespan
├── cli.py                       # CLI operativa (sync, init-db, validate)
├── core/config.py               # Configuración centralizada por variables de entorno
├── domain/                      # Entidades, validación Módulo 10/11, excepciones y puertos
├── application/use_cases.py     # Casos de uso independientes de FastAPI y PostgreSQL
├── infrastructure/
│   ├── db/                      # Pool psycopg2, pg_trgm, unaccent, advisory locks y sync_runs
│   ├── dgii/                    # Streaming HTTP, modo local y parser CSV/TXT dinámico
│   └── scheduler/               # Cronjob diario con APScheduler
└── presentation/
    ├── routers/                 # Endpoints REST (/api/v1/*, /api/*, /health) y UI web (/)
    ├── static/index.html        # Buscador web local (sin dependencias CDN externas)
    ├── schemas/                 # DTOs Pydantic v2 para Swagger UI
    ├── dependencies.py          # Inyección de casos de uso, rate limit y control sync local
    └── exception_handlers.py    # Mapeo de excepciones de dominio a códigos HTTP
```

## Despliegue rápido en tu empresa

### Opción A: Stack completo con Docker Compose (recomendado)

```bash
cp .env.example .env
# Edita DB_USER y DB_PASSWORD en .env antes de subir en servidor compartido
docker compose up -d --build
```

Luego abre en tu navegador:
- **Buscador Web (contabilidad / facturación):** `http://localhost:8000/`
- **Swagger UI (desarrolladores):** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`

> Al iniciar por primera vez con la base de datos vacía, el servicio responderá inmediatamente y descargará el padrón oficial en segundo plano. Puedes verificar el progreso en `GET /health` o `GET /api/v1/sync/status`.

### Opción B: Conectar a un PostgreSQL ya existente en la empresa

Si tu empresa ya cuenta con un servidor PostgreSQL (versión 14+ con permisos para `CREATE EXTENSION pg_trgm, unaccent`), solo levanta el contenedor `api` apuntando `DB_HOST` en `.env`:

```bash
docker compose up -d --no-deps api
```

### Opción C: Servidores sin acceso a internet (importación local / CLI)

Descarga `RNC_CONTRIBUYENTES.zip` desde otra máquina y ejecútalo por CLI o configura `DGII_LOCAL_ZIP_PATH`:

```bash
python -m app.cli sync --file /ruta/RNC_CONTRIBUYENTES.zip
```

## Variables de entorno

| Variable | Descripción | Valor por defecto |
| --- | --- | --- |
| `DB_HOST` | Host de PostgreSQL | `postgres` |
| `DB_PORT` | Puerto interno de PostgreSQL | `5432` |
| `DB_BIND_HOST` | Interfaz local donde Docker expone Postgres | `127.0.0.1` |
| `DB_EXTERNAL_PORT` | Puerto del host mapeado a Postgres | `5432` |
| `DB_USER` | Usuario de PostgreSQL | `dgii_user` |
| `DB_PASSWORD` | Contraseña de PostgreSQL | `dgii_password` |
| `DB_NAME` | Nombre de la base de datos | `dgii_rnc` |
| `DB_POOL_TIMEOUT_SECONDS` | Espera máxima por una conexión libre del pool (seg) | `5` |
| `DB_STATEMENT_TIMEOUT_MS` | Timeout para búsquedas de texto (ms) | `5000` |
| `API_PORT` | Puerto HTTP expuesto por la API | `8000` |
| `DGII_RNC_URL` | URL del ZIP oficial de la DGII | `https://dgii.gov.do/app/WebApps/Consultas/RNC/RNC_CONTRIBUYENTES.zip` |
| `DGII_LOCAL_ZIP_PATH` | Ruta a ZIP/CSV/TXT local (modo offline) | _(vacío)_ |
| `SYNC_HOUR` / `SYNC_MINUTE` | Horario diario de sincronización automática | `3` / `0` |
| `ENABLE_SCHEDULER` | Habilita cronjob en memoria (`false` si usas cron externo) | `true` |
| `ENABLE_MANUAL_SYNC` | Habilita `POST /api/v1/sync` vía HTTP | `true` |
| `ALLOW_REMOTE_SYNC` | Permite `POST /api/v1/sync` desde IPs no locales (proteger en proxy/gateway) | `false` |
| `CORS_ORIGINS` | Orígenes permitidos separados por coma | `*` |
| `BATCH_MAX_ITEMS` | Máximo de RNCs por llamada en `/rnc/batch` | `200` |
| `STATS_CACHE_TTL_SECONDS` | Caché en memoria (seg) para `/stats` y conteo de `/health` | `300` |
| `LOOKUP_CACHE_SIZE` / `LOOKUP_CACHE_TTL_SECONDS` | Caché LRU en memoria para consultas exactas por RNC | `2048` / `300` |
| `RATE_LIMIT_ENABLED` | Activa limitación de tasa por IP (`429 Too Many Requests`) | `true` |
| `TRUST_PROXY_HEADERS` | Lee IP real desde `X-Forwarded-For`/`X-Real-IP` detrás de proxy | `false` |
| `RATE_LIMIT_LOOKUP_PER_MINUTE` | Consultas exactas/validar/stats por minuto por IP | `120` |
| `RATE_LIMIT_SEARCH_PER_MINUTE` | Búsquedas por razón social por minuto por IP | `30` |
| `RATE_LIMIT_BATCH_PER_MINUTE` | Consultas en lote por minuto por IP | `15` |
| `RATE_LIMIT_SYNC_PER_MINUTE` | Solicitudes de sincronización por minuto por IP | `5` |
| `TZ` | Zona horaria del contenedor | `America/Santo_Domingo` |

## Endpoints principales (`/api/v1`)

> Los endpoints bajo `/api/...` también se mantienen activos como alias retrocompatibles.

| Método | Ruta | Descripción |
| --- | --- | --- |
| `GET` | `/` | Interfaz web local para consultas individuales y lotes 606/607 |
| `GET` | `/api/v1/rnc/{rnc}` | Consulta exacta por RNC o Cédula (con o sin guiones) |
| `GET` | `/api/v1/rnc/{rnc}/validar` | Valida dígito verificador (Módulo 11 / Módulo 10) y verifica registro |
| `GET` | `/api/v1/rnc/{rnc}/historial` | Consulta cambios históricos del contribuyente entre sincronizaciones |
| `POST` | `/api/v1/rnc/batch` | Consulta múltiple por lista JSON de RNCs/Cédulas |
| `GET` | `/api/v1/buscar?query=...` | Búsqueda inteligente (`query` y/o `actividad`, RNC parcial, `estatus`, `regimen`) con paginación |
| `GET` | `/api/v1/stats` | Total de contribuyentes, desglose por estatus/régimen y último sync |
| `POST` | `/api/v1/sync` | Dispara sincronización en segundo plano (`?force=true` ignora ETag/SHA-256) |
| `GET` | `/api/v1/sync/status` | Estado actual y resultado de la última sincronización |
| `GET` | `/health` | Salud del servicio, conexión PostgreSQL y estado de carga inicial |

## Ejemplos de integración

### cURL

```bash
# Consultar RNC
curl http://localhost:8000/api/v1/rnc/130-66304-1

# Validar dígito verificador + registro
curl http://localhost:8000/api/v1/rnc/130-66304-1/validar

# Búsqueda por razón social o actividad económica
curl "http://localhost:8000/api/v1/buscar?query=clinica&estatus=ACTIVO&limit=10"
curl "http://localhost:8000/api/v1/buscar?actividad=ferreteria&estatus=ACTIVO&limit=10"

# Historial de cambios de un RNC
curl http://localhost:8000/api/v1/rnc/130-66304-1/historial

# Consulta en lote (606/607)
curl -X POST http://localhost:8000/api/v1/rnc/batch \
  -H "Content-Type: application/json" \
  -d '{"rncs": ["130-66304-1", "101010101"]}'
```

### Python

```python
import requests

resp = requests.get("http://localhost:8000/api/v1/rnc/130-66304-1", timeout=5)
resp.raise_for_status()
print(resp.json())
```

### JavaScript / TypeScript

```javascript
const res = await fetch("http://localhost:8000/api/v1/rnc/130-66304-1");
if (res.ok) {
  const data = await res.json();
  console.log(data.razon_social, data.estatus);
}
```

### C# (.NET)

```csharp
using var client = new HttpClient();
var json = await client.GetStringAsync("http://localhost:8000/api/v1/rnc/130-66304-1");
Console.WriteLine(json);
```

### PHP

```php
$json = file_get_contents("http://localhost:8000/api/v1/rnc/130-66304-1");
$data = json_decode($json, true);
echo $data["razon_social"] . " - " . $data["estatus"];
```

## Pruebas y desarrollo

```bash
# Instalar dependencias de desarrollo (incluye pytest, ruff, mypy, httpx)
make install-dev

# Ejecutar pruebas unitarias y HTTP
make test

# Ejecutar pruebas de integración contra PostgreSQL 18 real
RUN_POSTGRES_TESTS=true DB_HOST=localhost make test-int
```

## Licencia

Distribuido bajo la licencia [MIT](LICENSE).
