"""Unit tests for Loxone UDP discovery payload."""

import json
import unittest
from loxone_musicserver.config import ServerConfig
from loxone_musicserver.loxone.discovery import LoxoneUdpDiscoveryProtocol


class TestDiscovery(unittest.TestCase):
    def test_discovery_payload_structure(self):
        config = ServerConfig(
            server_name="Test-AudioServer",
            mac_address="50:4F:94:11:22:33",
            app_port=7091,
            firmware_version_number="17.1.05.05",
        )
        protocol = LoxoneUdpDiscoveryProtocol(config)
        data = json.loads(protocol.response_payload.decode("utf-8"))

        self.assertEqual(data["t"], "Audioserver")
        self.assertEqual(data["tv"], "3")
        self.assertEqual(data["n"], "Test-AudioServer")
        self.assertEqual(data["s"], "50:4F:94:11:22:33")
        self.assertEqual(data["v"], "17.1.05.05")
        self.assertEqual(data["p"], "7091")
        self.assertEqual(data["di"], "1")


if __name__ == "__main__":
    unittest.main()
