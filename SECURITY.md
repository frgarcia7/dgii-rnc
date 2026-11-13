# Política de Seguridad

## Recomendaciones para despliegue en empresas

1. **Credenciales:** Cambia `DB_USER` y `DB_PASSWORD` en tu archivo `.env` antes de levantar el servicio en un servidor de red o producción.
2. **Aislamiento de PostgreSQL:** Por defecto `docker-compose.yml` enlaza PostgreSQL en `127.0.0.1` (`DB_BIND_HOST=127.0.0.1`). No expongas el puerto `5432` hacia redes no confiables.
3. **Protección de sincronización y consultas:**
   - Configura `SYNC_API_KEY` para evitar que usuarios internos disparen descargas manuales en `POST /api/v1/sync` (o establece `ENABLE_MANUAL_SYNC=false`).
   - Configura `API_KEY` si expones la API fuera de tu red local (por ejemplo entre sucursales sin VPN) y restringe `CORS_ORIGINS`.
4. **TLS / HTTPS:** Si expones el servicio a múltiples equipos o fuera de localhost, coloca un proxy inverso (Nginx, Caddy o Traefik) con HTTPS delante de FastAPI.

## Reporte de vulnerabilidades

Si detectas una vulnerabilidad de seguridad, por favor abre un *Security Advisory* privado en GitHub en lugar de publicar detalles de explotación en un issue público.

