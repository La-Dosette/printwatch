import unittest
from unittest.mock import patch

import app as printwatch
import connectors


class HostTests(unittest.TestCase):
    def test_normalize_plain_host(self):
        self.assertEqual(
            printwatch.normalize_host("192.168.1.40"),
            ("192.168.1.40", "192.168.1.40", None),
        )

    def test_normalize_url_with_port_and_path(self):
        self.assertEqual(
            printwatch.normalize_host("http://192.168.1.40:7125/printer/info"),
            ("192.168.1.40:7125", "192.168.1.40", "http://192.168.1.40:7125"),
        )


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.client = printwatch.app.test_client()

    def test_detect_requires_host(self):
        res = self.client.get("/api/detect")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"], "Host is required")

    def test_detect_returns_stable_payload(self):
        detected = {
            "type": "moonraker",
            "host": "printer",
            "base": "http://printer:7125",
            "reason": "Moonraker found on port 7125.",
        }
        with patch.object(printwatch, "detect_printer", return_value=detected), \
                patch.object(printwatch, "discover_webcam", return_value="http://printer/webcam"):
            res = self.client.get("/api/detect?host=printer")

        data = res.get_json()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["type"], "moonraker")
        self.assertTrue(data["capabilities"]["controls"])
        self.assertEqual(data["reason"], "Moonraker found on port 7125.")

    def test_detect_tuple_wrapper(self):
        with patch.object(printwatch, "detect_printer", return_value={
            "type": "prusalink",
            "base": "http://printer",
        }):
            self.assertEqual(printwatch.detect_protocol("printer"), ("prusalink", "http://printer"))

    def test_status_requires_host(self):
        res = self.client.get("/api/status")
        self.assertEqual(res.status_code, 400)
        self.assertEqual(res.get_json()["error"], "Host is required")

    def test_status_returns_defaults_and_capabilities(self):
        payload = {"online": True, "state": "printing", "progress": 0.5}
        with patch.object(printwatch, "fetch_status", return_value=payload):
            res = self.client.get("/api/status?host=printer&type=moonraker")

        data = res.get_json()
        self.assertEqual(res.status_code, 200)
        self.assertTrue(data["ok"])
        self.assertEqual(data["type"], "moonraker")
        self.assertEqual(data["temps"], {})
        self.assertTrue(data["capabilities"]["stats"])

    def test_new_protocol_capabilities(self):
        self.assertTrue(printwatch.protocol_capabilities("prusalink")["needs_credentials"])
        self.assertFalse(printwatch.protocol_capabilities("creality_lan")["monitoring"])


class ConnectorTests(unittest.TestCase):
    def test_missing_prusalink_key_returns_clear_error(self):
        status = connectors.fetch_status({"type": "prusalink", "base_url": "http://printer", "apikey": ""})
        self.assertEqual(status["error"], "PrusaLink API key required")

    def test_unsupported_detected_protocol_returns_clear_error(self):
        status = connectors.fetch_status({"type": "creality_lan", "base_url": "ws://printer:9999"})
        self.assertEqual(status["state"], "detected")
        self.assertIn("Creality LAN detected", status["error"])


if __name__ == "__main__":
    unittest.main()
