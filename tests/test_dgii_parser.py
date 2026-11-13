"""Pruebas unitarias para el parser ZIP/CSV/TXT de la DGII."""

from __future__ import annotations

import io
import unittest
import zipfile

from app.infrastructure.dgii.parser import (
    iter_zip_batches,
    parse_csv_row,
    parse_pipe_line,
)


class TestDgiiParser(unittest.TestCase):
    def test_parse_csv_row_handles_commas_and_skips_header(self) -> None:
        self.assertIsNone(
            parse_csv_row(["RNC", "RAZÓN SOCIAL", "ACTIVIDAD ECONÓMICA"])
        )

        entity = parse_csv_row(
            [
                " 130663041 ",
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
            "RNC,RAZÓN SOCIAL,ACTIVIDAD ECONÓMICA,FECHA DE INICIO OPERACIONES,ESTADO,RÉGIMEN DE PAGO\n"
            '"130663041","EMPRESA ORIGINAL SRL","INSTALACIONES, MANTENIMIENTO","01/12/2009","ACTIVO","NORMAL"\n'
            '"130663041","EMPRESA ACTUALIZADA SRL","INSTALACIONES, MANTENIMIENTO","01/12/2009","SUSPENDIDO","NORMAL"\n'
            '"101010101","COMPAÑÍA NÚÑEZ SRL","COMERCIO","10/05/2020","ACTIVO","RST"\n'
        )

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "RNC_Contribuyentes_Actualizado.csv",
                csv_text.encode("iso-8859-1"),
            )
        buf.seek(0)

        batches = list(iter_zip_batches(buf, batch_size=10))
        self.assertEqual(len(batches), 1)
        items = {item.rnc: item for item in batches[0]}
        self.assertEqual(len(items), 2)
        self.assertEqual(items["130663041"].razon_social, "EMPRESA ACTUALIZADA SRL")
        self.assertEqual(items["130663041"].estatus, "SUSPENDIDO")
        self.assertEqual(items["101010101"].razon_social, "COMPAÑÍA NÚÑEZ SRL")

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

