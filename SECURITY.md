# Política de Seguridad

## Recomendaciones para despliegue público y en empresas

1. **Credenciales:** Cambia `DB_USER` y `DB_PASSWORD` en tu archivo `.env` antes de levantar el servicio en un servidor de red o producción.
2. **Aislamiento de PostgreSQL:** Por defecto `docker-compose.yml` enlaza PostgreSQL en `127.0.0.1` (`DB_BIND_HOST=127.0.0.1`). No expongas el puerto `5432` hacia redes no confiables.
3. **Protección de sincronización (`POST /api/v1/sync`):**
   - Por defecto `ALLOW_REMOTE_SYNC=false`, por lo que `POST /api/v1/sync` solo acepta solicitudes locales (`localhost` sin cabeceras proxy externas).
   - En producción pública, puedes establecer `ENABLE_MANUAL_SYNC=false` y sincronizar únicamente con el cron interno o `python -m app.cli sync`, o habilitar `ALLOW_REMOTE_SYNC=true` protegiendo la ruta `/api/*/sync` en tu proxy inverso/API Gateway.
4. **Autenticación y políticas corporativas:** Este proyecto provee la base open-source sin imponer un esquema de autenticación específico en la aplicación. Si tu empresa requiere API Keys, JWT/OAuth2, mTLS o listas blancas de IP, impleméntalo en el proxy inverso, API Gateway o capa corporativa según tus políticas.
5. **Rate Limiting y Proxy Inverso (TLS / HTTPS):**
   - El servicio incluye limitación de tasa por IP (`RATE_LIMIT_ENABLED=true`) para consultas, búsquedas, lotes y sincronización.
   - Coloca siempre un proxy inverso con TLS (Nginx, Caddy o Traefik) delante de FastAPI.
   - Activa `TRUST_PROXY_HEADERS=true` **únicamente** cuando tu proxy inverso sobrescriba/filtre `X-Forwarded-For` / `X-Real-IP` para evitar suplantación de IP por clientes directos.

## Reporte de vulnerabilidades

Si detectas una vulnerabilidad de seguridad, por favor abre un *Security Advisory* privado en GitHub en lugar de publicar detalles de explotación en un issue público.
