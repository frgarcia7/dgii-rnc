# Política de Seguridad

## Recomendaciones para despliegue público y en empresas

1. **Credenciales:** Cambia `DB_USER` y `DB_PASSWORD` en tu archivo `.env` antes de levantar el servicio en un servidor de red o producción.
2. **Aislamiento de PostgreSQL:** Por defecto `docker-compose.yml` enlaza PostgreSQL en `127.0.0.1` (`DB_BIND_HOST=127.0.0.1`). No expongas el puerto `5432` hacia redes no confiables.
3. **Protección de sincronización (`POST /api/v1/sync`):**
   - Si no defines `SYNC_API_KEY` (ni `API_KEY`), la API bloquea por defecto las ejecuciones de `POST /api/v1/sync` que provengan de IPs externas o proxys públicos (`403 Forbidden`), permitiendo solo origen local (`localhost`).
   - En producción pública, configura `SYNC_API_KEY` con un secreto largo o establece `ENABLE_MANUAL_SYNC=false` y sincroniza mediante el cron interno o `python -m app.cli sync`.
4. **Rate Limiting y Proxy Inverso (TLS / HTTPS):**
   - El servicio incluye limitación de tasa por IP (`RATE_LIMIT_ENABLED=true`) para consultas, búsquedas, lotes y sincronización.
   - Coloca siempre un proxy inverso con TLS (Nginx, Caddy o Traefik) delante de FastAPI.
   - Activa `TRUST_PROXY_HEADERS=true` **únicamente** cuando tu proxy inverso sobrescriba/filtre `X-Forwarded-For` / `X-Real-IP` para evitar suplantación de IP por clientes directos.
5. **CORS y API Key de lectura:** Si deseas restringir el acceso o aumentar cuotas para clientes integrados, configura `API_KEY` (multiplica el rate limit por `RATE_LIMIT_API_KEY_MULTIPLIER`) y restringe `CORS_ORIGINS`.

## Reporte de vulnerabilidades

Si detectas una vulnerabilidad de seguridad, por favor abre un *Security Advisory* privado en GitHub en lugar de publicar detalles de explotación en un issue público.
