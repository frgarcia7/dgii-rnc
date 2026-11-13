"""CLI operativa para sincronización manual, importación offline y validación."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import replace

from app.application.use_cases import SyncRncCatalogUseCase
from app.core.config import Settings
from app.domain.entities import validate_rnc_format
from app.infrastructure.db.connection import PostgresConnectionManager
from app.infrastructure.db.postgres_repository import PostgresContribuyenteRepository
from app.infrastructure.dgii.http_zip_source import HttpDgiiZipSource


def _cmd_sync(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    if args.file:
        settings = replace(
            settings,
            sync=replace(settings.sync, local_zip_path=args.file),
        )

    conn_manager = PostgresConnectionManager(settings.database)
    try:
        repo = PostgresContribuyenteRepository(conn_manager)
        repo.init_schema()
        source = HttpDgiiZipSource(
            source_url=settings.sync.source_url,
            timeout=(settings.sync.http_connect_timeout, settings.sync.http_read_timeout),
            retries=settings.sync.http_retries,
            local_zip_path=settings.sync.local_zip_path,
        )
        sync_uc = SyncRncCatalogUseCase(
            repository=repo,
            source=source,
            batch_size=settings.sync.batch_size,
        )
        report = sync_uc.execute(force=args.force)
        print(
            json.dumps(
                {
                    "status": report.status,
                    "processed": report.processed,
                    "changed": report.changed,
                    "elapsed_seconds": report.elapsed_seconds,
                    "message": report.message,
                },
                ensure_ascii=False,
            )
        )
        return 0
    finally:
        conn_manager.close()


def _cmd_init_db(_args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    conn_manager = PostgresConnectionManager(settings.database)
    try:
        repo = PostgresContribuyenteRepository(conn_manager)
        repo.init_schema()
        print("Esquema inicializado correctamente.")
        return 0
    finally:
        conn_manager.close()


def _cmd_validate(args: argparse.Namespace) -> int:
    result = validate_rnc_format(args.rnc)
    print(
        json.dumps(
            {
                "rnc": result.rnc,
                "rnc_formateado": result.rnc_formateado,
                "tipo": result.tipo,
                "formato_valido": result.formato_valido,
                "digito_verificador_valido": result.digito_verificador_valido,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if result.digito_verificador_valido else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli",
        description="Herramienta de administración local para DGII RNC API.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sync_parser = sub.add_parser(
        "sync", help="Sincroniza el padrón DGII desde HTTP o un archivo local."
    )
    sync_parser.add_argument(
        "--file",
        help="Ruta a un archivo local .zip, .csv o .txt (útil sin acceso a internet).",
    )
    sync_parser.add_argument(
        "--force",
        action="store_true",
        help="Ignora caché ETag/SHA-256 y reprocesa todos los registros.",
    )
    sync_parser.set_defaults(func=_cmd_sync)

    init_parser = sub.add_parser(
        "init-db", help="Crea extensiones, tablas e índices en PostgreSQL."
    )
    init_parser.set_defaults(func=_cmd_init_db)

    val_parser = sub.add_parser(
        "validate", help="Valida formato y dígito verificador de un RNC o Cédula."
    )
    val_parser.add_argument("rnc", help="Número de RNC o Cédula (con o sin guiones).")
    val_parser.set_defaults(func=_cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())

