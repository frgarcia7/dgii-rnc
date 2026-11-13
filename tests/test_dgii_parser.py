"""Pruebas unitarias para el parser ZIP/CSV/TXT y fuente HTTP/local DGII."""

from __future__ import annotations

import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.infrastructure.dgii.http_zip_source import HttpDgiiZipSource
from app.infrastructure.dgii.parser import (
    iter_source_batches,
    iter_zip_batches,
    parse_csv_row,
    parse_pipe_line,
)


def _build_zip_bytes(filename: str, content: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(filename, content)
    return buf.getvalue()


class TestDgiiParser(unittest.TestCase):
    def test_parse_csv_row_handles_commas_and_skips_header(self) -> None:
        self.assertIsNone(
            parse_csv_row(["RNC", "RAZÓN SOCIAL", "ACTIVIDAD ECONÓMICA"])
        )

        entity = parse_csv_row(
            [
                " 130-66304-1 ",
                "CONTRATISTAS DE INFRAESTRUCTURAS DE TELEFONOS ELECTRICIDAD Y CABLE S R L",
                "INSTALACIONES ELÉCTRICAS (INCL. ANTENAS, PARARRAYOS, SISTEMAS)",
                "01/12/2009",
                "SUSPENDIDO",
                "NORMAL",
            ]
        )
        self.assertIsNotNone(entity)
        assert entity is not None
        self.assertEqual(entity.rnc, "130663041")
        self.assertTrue(entity.razon_social.endswith("CABLE S R L"))
        self.assertIn("ANTENAS, PARARRAYOS, SISTEMAS", entity.actividad_economica)
        self.assertEqual(entity.fecha_inicio_operaciones, "01/12/2009")
        self.assertEqual(entity.estatus, "SUSPENDIDO")
        self.assertEqual(entity.regimen, "NORMAL")

    def test_iter_zip_batches_reads_iso8859_csv_and_deduplicates(self) -> None:
        csv_text = (
            "RNC,RAZÓN SOCIAL,ACTIVIDAD ECONÓMICA,"
            "FECHA DE INICIO OPERACIONES,ESTADO,RÉGIMEN DE PAGO\n"
            '"130663041","EMPRESA ORIGINAL SRL","INSTALACIONES, MANTENIMIENTO",'
            '"01/12/2009","ACTIVO","NORMAL"\n'
            '"130663041","EMPRESA ACTUALIZADA SRL","INSTALACIONES, MANTENIMIENTO",'
            '"01/12/2009","SUSPENDIDO","NORMAL"\n'
            '"101010101","COMPAÑÍA NÚÑEZ SRL","COMERCIO","10/05/2020","ACTIVO","RST"\n'
        )
        buf = io.BytesIO(
            _build_zip_bytes(
                "RNC_Contribuyentes_Actualizado.csv", csv_text.encode("iso-8859-1")
            )
        )

        batches = list(iter_zip_batches(buf, batch_size=10))
        self.assertEqual(len(batches), 1)
        items = {item.rnc: item for item in batches[0]}
        self.assertEqual(len(items), 2)
        self.assertEqual(items["130663041"].razon_social, "EMPRESA ACTUALIZADA SRL")
        self.assertEqual(items["130663041"].estatus, "SUSPENDIDO")
        self.assertEqual(items["101010101"].razon_social, "COMPAÑÍA NÚÑEZ SRL")

    def test_iter_zip_batches_handles_reordered_csv_headers(self) -> None:
        csv_text = (
            "ESTADO,RAZÓN SOCIAL,RNC/CÉDULA,RÉGIMEN,ACTIVIDAD ECONÓMICA,FECHA INICIO OPERACIONES\n"
            '"ACTIVO","EMPRESA REORDENADA SRL","130663041","RST","SERVICIOS","05/01/2022"\n'
        )
        buf = io.BytesIO(_build_zip_bytes("custom.csv", csv_text.encode("utf-8-sig")))

        batches = list(iter_zip_batches(buf, batch_size=10))
        self.assertEqual(len(batches), 1)
        item = batches[0][0]
        self.assertEqual(item.rnc, "130663041")
        self.assertEqual(item.razon_social, "EMPRESA REORDENADA SRL")
        self.assertEqual(item.estatus, "ACTIVO")
        self.assertEqual(item.regimen, "RST")
        self.assertEqual(item.actividad_economica, "SERVICIOS")
        self.assertEqual(item.fecha_inicio_operaciones, "05/01/2022")

    def test_iter_source_batches_supports_plain_csv_and_spooled_zip(self) -> None:
        csv_bytes = (
            "RNC,RAZÓN SOCIAL,ACTIVIDAD ECONÓMICA,"
            "FECHA DE INICIO OPERACIONES,ESTADO,RÉGIMEN DE PAGO\n"
            '"101010101","EMPRESA PLANA SRL","COMERCIO","01/01/2020","ACTIVO","NORMAL"\n'
        ).encode("utf-8")

        plain_stream = io.BytesIO(csv_bytes)
        plain_batches = list(
            iter_source_batches(plain_stream, batch_size=10, filename_hint="datos.csv")
        )
        self.assertEqual(len(plain_batches[0]), 1)
        self.assertEqual(plain_batches[0][0].rnc, "101010101")

        zip_bytes = _build_zip_bytes("rnc.csv", csv_bytes)
        with tempfile.SpooledTemporaryFile(max_size=1024) as spool:
            spool.write(zip_bytes)
            spool_batches = list(iter_source_batches(spool, batch_size=10))
            self.assertEqual(len(spool_batches[0]), 1)
            self.assertEqual(spool_batches[0][0].razon_social, "EMPRESA PLANA SRL")

    def test_parse_pipe_line_fallback_for_txt(self) -> None:
        line = "130663041|EMPRESA SRL|COMERCIAL|ACTIVIDAD|||||01/12/2009|ACTIVO|NORMAL"
        entity = parse_pipe_line(line)
        self.assertIsNotNone(entity)
        assert entity is not None
        self.assertEqual(entity.rnc, "130663041")
        self.assertEqual(entity.actividad_economica, "ACTIVIDAD")
        self.assertEqual(entity.fecha_inicio_operaciones, "01/12/2009")
        self.assertEqual(entity.estatus, "ACTIVO")
        self.assertEqual(entity.regimen, "NORMAL")

    def test_local_file_source_respects_sha256_cache(self) -> None:
        csv_bytes = (
            "RNC,RAZÓN SOCIAL,ACTIVIDAD ECONÓMICA,"
            "FECHA DE INICIO OPERACIONES,ESTADO,RÉGIMEN DE PAGO\n"
            '"101010101","LOCAL SRL","COMERCIO","01/01/2020","ACTIVO","NORMAL"\n'
        ).encode("utf-8")

        with tempfile.TemporaryDirectory() as tmpdir:
            zip_path = Path(tmpdir) / "local_dgii.zip"
            zip_path.write_bytes(_build_zip_bytes("rnc.csv", csv_bytes))

            source = HttpDgiiZipSource(
                source_url="https://example.invalid/rnc.zip",
                local_zip_path=str(zip_path),
            )
            with source.fetch(batch_size=10) as res1:
                self.assertFalse(res1.not_modified)
                batches = list(res1.batches)
                self.assertEqual(len(batches[0]), 1)
                first_hash = res1.content_hash

            with source.fetch(batch_size=10, known_hash=first_hash) as res2:
                self.assertTrue(res2.not_modified)

            with source.fetch(batch_size=10, known_hash=first_hash, force=True) as res3:
                self.assertFalse(res3.not_modified)
                self.assertEqual(len(list(res3.batches)[0]), 1)

    def test_http_source_handles_304_and_streaming_zip(self) -> None:
        zip_bytes = _build_zip_bytes(
            "rnc.csv",
            (
                "RNC,RAZÓN SOCIAL,ACTIVIDAD ECONÓMICA,"
                "FECHA DE INICIO OPERACIONES,ESTADO,RÉGIMEN DE PAGO\n"
                '"130663041","STREAM SRL","SERVICIOS","01/01/2020","ACTIVO","NORMAL"\n'
            ).encode("utf-8"),
        )

        resp_304 = MagicMock()
        resp_304.status_code = 304
        resp_304.__enter__ = MagicMock(return_value=resp_304)
        resp_304.__exit__ = MagicMock(return_value=False)

        source = HttpDgiiZipSource(source_url="https://dgii.gov.do/test.zip", retries=0)
        with patch("requests.Session.get", return_value=resp_304):
            with source.fetch(batch_size=10, known_etag='"abc"') as res:
                self.assertTrue(res.not_modified)

        resp_200 = MagicMock()
        resp_200.status_code = 200
        resp_200.headers = {"ETag": '"new-tag"'}
        resp_200.iter_content = MagicMock(return_value=[zip_bytes[:30], zip_bytes[30:]])
        resp_200.__enter__ = MagicMock(return_value=resp_200)
        resp_200.__exit__ = MagicMock(return_value=False)

        with patch("requests.Session.get", return_value=resp_200):
            with source.fetch(batch_size=10) as res:
                self.assertFalse(res.not_modified)
                self.assertEqual(res.etag, '"new-tag"')
                items = list(res.batches)[0]
                self.assertEqual(items[0].rnc, "130663041")
                self.assertEqual(items[0].razon_social, "STREAM SRL")
