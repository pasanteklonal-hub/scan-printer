"""Pruebas unitarias para los módulos de servicio y base de datos del escáner."""

import json
import tempfile
import unittest
from pathlib import Path

from scanner_db import init_db, load_ips, load_last_results, load_printers, save_ips, save_scan_results, sanitize_for_json
from scanner_service import IP_REGEX, _extraer_numero_serie, limpiar_ip
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


if __name__ == "__main__":
    unittest.main()
