"""Pruebas unitarias para los módulos de servicio y base de datos del escáner."""

import json
import asyncio
import tempfile
import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path

import actualizar_listado
from prediccion_toners import (
    construir_reporte,
    construir_reporte_proxima_compra,
    consumo_historico_por_impresora,
    consumo_historico_por_modelo,
    fecha_desde_nombre,
    niveles_toner,
)
from scanner_db import (
    init_db,
    load_ips,
    load_last_results,
    load_printer_commands,
    load_printers,
    save_ips,
    save_printer_commands,
    save_scan_results,
    sanitize_for_json,
)
from scanner_service import (
    IP_REGEX,
    _extraer_numero_serie,
    consultar_con_comando,
    consultar_todas,
    consultar_una_impresora,
    limpiar_ip,
)
from scriptGeneral import (
    extraer_numero_serie_html,
    extraer_numero_serie_sysdescr,
    normalizar_mac_snmp,
)


class TestScannerService(unittest.TestCase):
    def test_limpiar_ip_removes_scheme_and_trailing_slash(self):
        self.assertEqual(limpiar_ip("https://192.168.1.10/"), "192.168.1.10")

    def test_ip_regex_accepts_ipv4(self):
        self.assertTrue(IP_REGEX.match("192.168.0.1"))

    def test_extracts_samsung_serial_from_identity_html(self):
        class PrinterDummy:
            def raw(self):
                return '<div class="x-column" style="width:240px;">Z5THB8GC6F001EM </div>'

        self.assertEqual(_extraer_numero_serie(PrinterDummy()), "Z5THB8GC6F001EM")

    def test_extracts_hp_serial_from_option_html(self):
        html = "<option value=\"x\">Número de serie de la impresora:  CNHXJ23710</option>"
        self.assertEqual(extraer_numero_serie_html(html), "CNHXJ23710")

    def test_extracts_serial_from_snmp_sysdescr(self):
        sysdescr = (
            "Samsung ML-371x Series; V2.10.01.36;NIC V4.01.16;"
            "S/N Z5THB8GC6F001EM"
        )
        self.assertEqual(
            extraer_numero_serie_sysdescr(sysdescr), "Z5THB8GC6F001EM"
        )

    def test_extracts_hp_serial_from_snmp_sysdescr(self):
        sysdescr = "HP ETHERNET MULTI-ENVIRONMENT,SN:BRBSHCQ3PB,FN:4K004T3"
        self.assertEqual(
            extraer_numero_serie_sysdescr(sysdescr), "BRBSHCQ3PB"
        )

    def test_uses_hp_serial_snmp_oid_without_extra_suffix(self):
        import scriptGeneral

        self.assertEqual(scriptGeneral.OID_NUMERO_SERIE, "1.3.6.1.2.1.43.5.1.1.17.1")

    def test_normalizes_snmp_mac_to_excel_format(self):
        self.assertEqual(
            normalizar_mac_snmp("48 0F CF F9 41 7B"),
            "48:0F:CF:F9:41:7B",
        )

    def test_normalizes_snmp_mac_bytes_to_excel_format(self):
        self.assertEqual(
            normalizar_mac_snmp(bytes.fromhex("480FCFF9417B")),
            "48:0F:CF:F9:41:7B",
        )

    def test_normalizes_snmp_octet_string_to_excel_format(self):
        class OctetStringDummy:
            def asOctets(self):
                return bytes.fromhex("480FCFF9417B")

        self.assertEqual(
            normalizar_mac_snmp(OctetStringDummy()),
            "48:0F:CF:F9:41:7B",
        )

    def test_consultar_todas_continues_after_timeout(self):
        async def consultar(ip, timeout, marca):
            if ip == "192.168.0.10":
                await asyncio.sleep(timeout + 1)
            return {"ip": ip, "marca": marca, "online": True, "error": None}

        async def run_scan():
            with patch("scanner_service.consultar_una_impresora", new=AsyncMock(side_effect=consultar)) as mock:
                resultados = await consultar_todas(
                    ["192.168.0.10", "192.168.0.20"], timeout=2
                )
            return resultados, mock

        resultados, mock = asyncio.run(run_scan())

        self.assertEqual(mock.await_count, 2)
        self.assertIn("Timeout", resultados[0]["error"])
        self.assertIsNone(resultados[1]["error"])

    def test_otra_usa_consulta_snmp_estandar(self):
        resultado_snmp = {
            "ip": "192.168.0.30",
            "marca": "SNMP",
            "modelo": "Ricoh IM C3000",
            "toner": {"Black": 75},
            "error": None,
        }

        async def run_query():
            with patch(
                "scanner_service.obtener_estado_toner",
                new=AsyncMock(return_value=resultado_snmp),
            ) as consultar_snmp:
                resultado = await consultar_una_impresora(
                    "192.168.0.30", timeout=5, marca="Otra"
                )
            return resultado, consultar_snmp

        resultado, consultar_snmp = asyncio.run(run_query())

        consultar_snmp.assert_awaited_once_with("192.168.0.30", timeout=5)
        self.assertEqual(resultado["marca"], "Otra")
        self.assertEqual(resultado["modelo"], "Ricoh IM C3000")

    def test_ricoh_usa_consulta_snmp_especifica(self):
        resultado_ricoh = {
            "ip": "192.168.0.35",
            "marca": "Ricoh",
            "modelo": "RICOH SP 3710SF V1.03",
            "toner": {"Black": 50},
            "paginas": {"impresiones_totales": 84426},
            "numero_serie": "5178ZB30564",
            "mac": "58:38:79:19:20:EA",
            "error": None,
        }

        async def run_query():
            with patch(
                "scanner_service.obtener_estado_ricoh",
                new=AsyncMock(return_value=resultado_ricoh),
            ) as consultar_ricoh:
                resultado = await consultar_una_impresora(
                    "192.168.0.35", timeout=8, marca="Ricoh"
                )
            return resultado, consultar_ricoh

        resultado, consultar_ricoh = asyncio.run(run_query())

        consultar_ricoh.assert_awaited_once_with("192.168.0.35", timeout=8)
        self.assertEqual(resultado["modelo"], "RICOH SP 3710SF V1.03")
        self.assertEqual(resultado["toner"]["Black"], 50)
        self.assertEqual(resultado["paginas"]["impresiones_totales"], 84426)

    def test_comando_normaliza_salida_json(self):
        async def run_query():
            return await consultar_con_comando(
                "192.168.0.40",
                "Lexmark",
                'printf \'{"modelo":"MX"}\'',
                ["modelo"],
                timeout=2,
            )

        resultado = asyncio.run(run_query())
        self.assertEqual(resultado["ip"], "192.168.0.40")
        self.assertEqual(resultado["marca"], "Lexmark")
        self.assertEqual(resultado["modelo"], "MX")
        self.assertEqual(resultado["toner"], {})
        self.assertIsNone(resultado["error"])

    def test_comando_rechaza_claves_declaradas_ausentes(self):
        resultado = asyncio.run(
            consultar_con_comando("192.168.0.40", "Lexmark", "printf '{}'", ["modelo"])
        )
        self.assertFalse(resultado["online"])
        self.assertIn("faltan claves declaradas", resultado["error"])

    def test_comando_valida_tipo_de_salida_declarado(self):
        resultado = asyncio.run(
            consultar_con_comando(
                "192.168.0.40",
                "Lexmark",
                'printf \'{"modelo":"MX","paginas":12}\'',
                [{"clave": "modelo", "tipo": "string"}, {"clave": "paginas", "tipo": "integer"}],
            )
        )
        self.assertIsNone(resultado["error"])
        self.assertEqual(resultado["paginas"], 12)

    def test_comando_rechaza_tipo_de_salida_incorrecto(self):
        resultado = asyncio.run(
            consultar_con_comando(
                "192.168.0.40", "Lexmark", 'printf \'{"paginas":"12"}\'',
                [{"clave": "paginas", "tipo": "integer"}],
            )
        )
        self.assertFalse(resultado["online"])
        self.assertIn("tipos inválidos", resultado["error"])


class TestPrediccionToners(unittest.TestCase):
    def test_extrae_fecha_del_snapshot(self):
        self.assertEqual(
            fecha_desde_nombre(Path("impresoras_20260916_144734.json")),
            datetime(2026, 9, 16, 14, 47, 34),
        )

    def test_normaliza_claves_de_cartuchos(self):
        self.assertEqual(
            niveles_toner({"black": 49, "Black Cartridge HP CB540A": 45, "1": 100}),
            {"black": 45.0},
        )

    def test_ordena_por_dias_y_calcula_fecha(self):
        historial = [
            (datetime(2026, 9, 16, 13), [{"numero_serie": "a", "mac": "AA", "modelo": "Modelo de prueba", "paginas": {"impresiones_totales": 100}}]),
            (datetime(2026, 9, 16, 14), [{"numero_serie": "a", "mac": "AA", "modelo": "Modelo de prueba", "paginas": {"impresiones_totales": 200}}]),
            (datetime(2026, 9, 16, 15), [{"numero_serie": "a", "mac": "AA", "modelo": "Modelo de prueba", "paginas": {"impresiones_totales": 300}}]),
        ]
        consumos = consumo_historico_por_impresora(historial)
        self.assertEqual(consumos["a"], 100.0)
        self.assertEqual(consumo_historico_por_modelo(historial)["modelo de prueba"], 2400.0)
        reporte = construir_reporte(
            [
                {"ip": "2", "numero_serie": "a", "mac": "AA", "marca": "Samsung", "modelo": "Samsung ML-371x Series", "toner": {"black": 50}},
            ],
            datetime(2026, 9, 16, 14, 47, 34),
            hoy=date(2026, 9, 16),
            consumos_hora=consumos,
            areas={"aa": "Administración"},
        )
        self.assertEqual(reporte.iloc[0]["Páginas Restantes Estimadas"], 1600)
        self.assertEqual(reporte.iloc[0]["Fecha Estimada de Agotamiento"], "2026-09-16 16:00")
        compra = construir_reporte_proxima_compra(reporte)
        self.assertEqual(list(compra.columns), ["Área", "Marca", "Modelo", "Agotamiento estimado", "Horas restantes (Días)"])
        self.assertEqual(compra.iloc[0]["Área"], "Administración")
        self.assertEqual(compra.iloc[0]["Horas restantes (Días)"], "16.1 (0.7)")


class TestActualizarListado(unittest.TestCase):
    def test_exportar_pdf_libreoffice_overwrites_existing_pdf(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            base_dir = Path(temp_dir)
            salida_xlsx = base_dir / "impresoras_al_dia.xlsx"
            salida_xlsx.write_bytes(b"xlsx")
            temporal_xlsx = base_dir / "__tmp_impresoras.xlsx"
            temporal_xlsx.write_bytes(b"tmp")
            pdf_viejo = base_dir / "impresoras_al_dia.pdf"
            pdf_viejo.write_bytes(b"viejo")
            pdf_tmp = base_dir / "__tmp_impresoras.pdf"
            pdf_tmp.write_bytes(b"nuevo")

            mock_wb = MagicMock()
            mock_sheet = MagicMock()
            mock_sheet.PAPERSIZE_A4 = "A4"
            mock_sheet.column_dimensions = {}
            mock_wb.active = mock_sheet
            mock_wb.__enter__ = MagicMock(return_value=mock_wb)
            mock_wb.__exit__ = MagicMock(return_value=False)

            with patch.object(actualizar_listado, "BASE_DIR", base_dir), \
                 patch.object(actualizar_listado, "ARCHIVO_SALIDA", salida_xlsx), \
                 patch("actualizar_listado.load_workbook", return_value=mock_wb), \
                 patch("actualizar_listado.shutil.which", side_effect=lambda cmd: "/usr/bin/soffice" if cmd in {"soffice", "libreoffice"} else None), \
                 patch("actualizar_listado.os.path.exists", side_effect=lambda path: str(path) == "/usr/bin/soffice"), \
                 patch("actualizar_listado.subprocess.run") as run_mock:
                actualizar_listado.exportar_pdf_libreoffice()

            run_mock.assert_called_once()
            self.assertTrue((base_dir / "impresoras_al_dia.pdf").exists())
            self.assertEqual((base_dir / "impresoras_al_dia.pdf").read_bytes(), b"nuevo")


class TestScannerDb(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "scanner_test.db"
        init_db(db_path=self.db_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_save_and_load_ips(self):
        save_ips(["192.168.0.10", "192.168.0.20"], db_path=self.db_path)
        self.assertEqual(load_ips(db_path=self.db_path), ["192.168.0.10", "192.168.0.20"])

    def test_save_and_load_printers_with_brand(self):
        save_ips([
            {"ip": "192.168.0.10", "marca": "Samsung"},
            {"ip": "192.168.0.20", "marca": "HP"},
        ], db_path=self.db_path)
        self.assertEqual(load_printers(db_path=self.db_path), [
            {"ip": "192.168.0.10", "marca": "Samsung"},
            {"ip": "192.168.0.20", "marca": "HP"},
        ])

    def test_save_and_load_results(self):
        results = [{"ip": "192.168.0.10", "modelo": "X", "toner": {"cyan": 80}, "error": None}]
        save_scan_results(results, db_path=self.db_path)
        self.assertEqual(load_last_results(db_path=self.db_path), results)

    def test_sanitize_for_json_converts_non_serializable_objects(self):
        class EstadoDummy:
            pass

        payload = [{"ip": "192.168.0.10", "estado": EstadoDummy(), "toner": {"cyan": 80}}]
        sanitized = sanitize_for_json(payload)

        self.assertEqual(sanitized[0]["estado"], str(payload[0]["estado"]))
        self.assertTrue(json.loads(json.dumps(sanitized)))

    def test_save_and_load_printer_commands(self):
        commands_path = Path(self.temp_dir.name) / "comandos.json"
        commands = [{"marca": "Lexmark", "comando": "echo {ip}", "salida": ["modelo"]}]
        save_printer_commands(commands, commands_path=commands_path)
        self.assertEqual(load_printer_commands(commands_path=commands_path), commands)


if __name__ == "__main__":
    unittest.main()
