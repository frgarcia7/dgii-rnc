# Guía de Contribución

¡Las contribuciones para mejorar esta herramienta comunitaria de consulta RNC en República Dominicana son bienvenidas!

## Flujo de trabajo local

1. Crea un fork y clona el repositorio.
2. Instala el entorno de desarrollo:
   ```bash
   make install-dev
   ```
3. Ejecuta pruebas unitarias y verificaciones antes de abrir un Pull Request:
   ```bash
   make test
   make lint
   make typecheck
   ```
4. Si tienes Docker disponible y quieres probar contra PostgreSQL 18 real:
   ```bash
   docker compose up -d postgres
   RUN_POSTGRES_TESTS=true DB_HOST=localhost make test-int
   ```

## Reglas de arquitectura

- `app/domain/`: entidades puras, reglas de validación (Módulo 10/11) y puertos (`Protocol`). Sin dependencias de FastAPI, `psycopg2` ni `requests`.
- `app/application/`: casos de uso desacoplados.
- `app/infrastructure/`: adaptadores concretos (PostgreSQL, descarga DGII, parser, scheduler).
- `app/presentation/`: routers HTTP, DTOs Pydantic, dependencias e interfaz web.

## Estándares

- Mantén funciones pequeñas y legibles.
- Agrega pruebas unitarias para cada bug corregido o nueva funcionalidad.
- Verifica que `docker compose config -q` siga pasando sin advertencias.

