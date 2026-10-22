"""Integration tests for Loxone handlers."""

import asyncio
import unittest
from loxone_musicserver.config import ServerConfig
from loxone_musicserver.loxone.crypto import LoxoneCrypto
from loxone_musicserver.loxone.handlers.config_handlers import ConfigHandlers
from loxone_musicserver.loxone.handlers.secure_handlers import SecureHandlers
from loxone_musicserver.loxone.handlers.zone_handlers import ZoneHandlers
from loxone_musicserver.loxone.websocket import LoxoneZoneState


class TestHandlers(unittest.TestCase):
    def setUp(self):
        self.config = ServerConfig()
        self.crypto = LoxoneCrypto()
        self.secure = SecureHandlers("TEST_SERIAL_123")
        self.cfg = ConfigHandlers(self.config, self.crypto)

    def test_secure_endpoints(self):
        async def run():
            res_hello = await self.secure.hello("secure/hello/test_pub_key")
            self.assertEqual(res_hello.name, "secure_hello")
            self.assertEqual(res_hello.payload["public_key"], "test_pub_key")
            self.assertTrue(res_hello.raw)

            # Test timestamp prefix and base64 with slashes
            res_hello_ts = await self.secure.hello("secure/hello/1792675208/LS0tLS1CRUdJTi/BDRVJUSUZJQ0FURS...")
            self.assertEqual(res_hello_ts.payload["public_key"], "LS0tLS1CRUdJTi/BDRVJUSUZJQ0FURS...")
            self.assertEqual(res_hello_ts.payload["error"], 0)
            self.assertTrue(res_hello_ts.raw)

            res_info = await self.secure.info_pairing("secure/info/pairing")
            self.assertEqual(res_info.payload["master"], "TEST_SERIAL_123")
            self.assertEqual(res_info.payload["error"], 0)
            self.assertTrue(res_info.raw)

            # Verify un-paired returns error -81
            sec_unpaired = SecureHandlers("")
            res_unpaired = await sec_unpaired.info_pairing("secure/info/pairing")
            self.assertEqual(res_unpaired.payload["error"], -81)
            self.assertEqual(res_unpaired.payload["master"], "")

            # Verify colons and hyphens in master serial are stripped
            sec_colons = SecureHandlers("50:4F:94:D0:1A:6D")
            self.assertEqual(sec_colons.clean_master, "504F94D01A6D")
            info_colons = await sec_colons.info_pairing("secure/info/pairing")
            self.assertEqual(info_colons.payload["master"], "504F94D01A6D")
            self.assertEqual(info_colons.payload["error"], 0)

            # Verify SecureHandlers linked with ServerConfig dynamically syncs
            cfg_dyn = ServerConfig(master_serial="50:4F:94:D0:1A:6D")
            sec_linked = SecureHandlers(config=cfg_dyn)
            self.assertEqual(sec_linked.clean_master, "504F94D01A6D")
            cfg_dyn.master_serial = "MS1A6D"
            self.assertEqual(sec_linked.clean_master, "MS1A6D")

            res_auth = await self.secure.authenticate("secure/authenticate")
            self.assertEqual(res_auth.payload["response"], "authentication successful")
            self.assertTrue(res_auth.raw)

            res_init = await self.secure.init("secure/init")
            self.assertEqual(res_init.name, "secure_init")
            self.assertIn("jwt", res_init.payload)
            self.assertTrue(res_init.raw)

        asyncio.run(run())

    def test_default_mac(self):
        cfg = ServerConfig()
        self.assertEqual(cfg.mac_address, "79:F8:74:AD:39:43")
        self.assertEqual(cfg.clean_mac, "79F874AD3943")
        self.assertEqual(cfg.formatted_mac, "79:F8:74:AD:39:43")

    def test_config_endpoints(self):
        async def run():
            res_key = await self.cfg.get_key("audio/cfg/getkey")
            self.assertEqual(res_key.name, "getkey")
            self.assertEqual(res_key.payload[0]["exp"], 65537)

            res_ready = await self.cfg.ready("audio/cfg/ready")
            self.assertIn("session", res_ready.payload)

            res_ident = await self.cfg.identify("audio/cfg/identify")
            self.assertIn("mac", res_ident.payload)
            self.assertEqual(res_ident.payload["mac"], self.config.clean_mac)
            self.assertNotIn(":", res_ident.payload["mac"])

            ident_ms = self.config.get_identification("msHttp")
            self.assertIn(f"MINISERVER V {self.config.firmware_version} {self.config.clean_mac}", ident_ms)
            self.assertNotIn(f"{self.config.formatted_mac}", ident_ms)

        asyncio.run(run())

    def test_zone_volume_parsing(self):
        called = {}

        async def send_cmd(zid, action, param):
            called["zid"] = zid
            called["action"] = action
            called["param"] = param

        state = LoxoneZoneState(playerid=1, name="Kitchen", volume=40)
        zh = ZoneHandlers(lambda zid: state if zid == 1 else None, send_cmd)

        async def run():
            # Relative increase +5
            await zh.dynamic_command("audio/1/volume/+5")
            self.assertEqual(called["param"], 45)

            # Relative decrease -10
            await zh.dynamic_command("audio/1/volume/-10")
            self.assertEqual(called["param"], 30)

            # Absolute set 65
            await zh.dynamic_command("audio/1/volume/65")
            self.assertEqual(called["param"], 65)

            # Next track
            res_next = await zh.dynamic_command("audio/1/queueplus")
            self.assertEqual(res_next.name, "queueplus")
            self.assertEqual(called["action"], "next")

        asyncio.run(run())

    def test_config_extraction_and_devicestartup(self):
        import base64
        import json

        async def run():
            # Create sample Loxone setconfig JSON with matching clean_mac
            clean_mac = self.config.clean_mac
            sample_config = [
                {
                    clean_mac: {
                        "uuid": "14f3b890-023a-11ee-be56-0242ac120002",
                        "name": "Audioserver 101",
                        "ip": "192.168.1.107",
                        "port": 80,
                        "host": "192.168.1.10",
                        "master": "504F94D01A6D",
                        "players": [],
                        "extensions": [],
                    }
                }
            ]
            raw_json = json.dumps(sample_config)
            b64_payload = base64.b64encode(raw_json.encode("utf-8")).decode("utf-8")

            res = await self.cfg.set_config(f"audio/cfg/setconfig/{b64_payload}")
            self.assertEqual(res.name, "setconfig")
            self.assertIsNotNone(res.payload["crc32"])

            # Verify ServerConfig was updated
            self.assertEqual(self.config.uuid, "14f3b890-023a-11ee-be56-0242ac120002")
            self.assertEqual(self.config.miniserver_ip, "192.168.1.107")
            self.assertEqual(self.config.master_serial, "504F94D01A6D")
            self.assertEqual(self.config.server_name, "Audioserver 101")

            # Verify diagnosis returns enriched info
            diag = await self.cfg.diagnosis("audio/cfg/diagnosis")
            self.assertEqual(diag.payload["uuid"], "14f3b890-023a-11ee-be56-0242ac120002")
            self.assertEqual(diag.payload["miniserver_ip"], "192.168.1.107")
            self.assertEqual(diag.payload["master_serial"], "504F94D01A6D")
            self.assertTrue(diag.payload["paired"])

            # Test trigger_startup returns status dictionary
            startup_res = await self.cfg.trigger_startup("audio/cfg/trigger_startup")
            self.assertIn("timestamp", startup_res.payload)
            self.assertEqual(startup_res.payload["url"], "http://192.168.1.107:80/dev/sps/devicestartup/14f3b890-023a-11ee-be56-0242ac120002")

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
