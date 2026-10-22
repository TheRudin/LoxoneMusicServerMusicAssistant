"""Tests for Dual-Protocol LoxoneServer and TLS multiplexing."""

import asyncio
import ssl
import tempfile
from pathlib import Path
import unittest
from aiohttp import ClientSession

from providers.loxone.config import ServerConfig
from providers.loxone.router import LoxoneRouter
from providers.loxone.server import LoxoneServer
from providers.loxone.websocket import LoxoneWebSocketNotifier
from providers.loxone.handlers.secure_handlers import SecureHandlers


class TestDualProtocolServer(unittest.TestCase):
    def test_secure_handlers_pairing_state(self):
        async def run():
            # Unpaired: error -81, master ""
            secure = SecureHandlers()
            res = await secure.info_pairing("secure/info/pairing")
            self.assertEqual(res.payload["error"], -81)
            self.assertEqual(res.payload["master"], "")

            # Paired: error 0 (paired to master), master "MS1A6D"
            secure.set_master("MS1A6D")
            res2 = await secure.info_pairing("secure/info/pairing")
            self.assertEqual(res2.payload["error"], 0)
            self.assertEqual(res2.payload["master"], "MS1A6D")

        asyncio.run(run())

    def test_dual_protocol_dispatch(self):
        async def run():
            with tempfile.TemporaryDirectory() as td:
                cfg = ServerConfig(
                    data_dir=Path(td),
                    app_port=18091,
                    ms_port=18095,
                    host="127.0.0.1",
                )
                router = LoxoneRouter()
                async def ping_handler(cmd, payload=None, match=None):
                    from providers.loxone.responses import build_response
                    return build_response(cmd, "ping", {"status": "ok"})
                router.register_prefix("audio/cfg/ping", ping_handler)

                notifier = LoxoneWebSocketNotifier(cfg, lambda: [], router)
                server = LoxoneServer(cfg, router, notifier)

                await server.start()

                client_ssl = ssl.create_default_context()
                client_ssl.check_hostname = False
                client_ssl.verify_mode = ssl.CERT_NONE

                async with ClientSession() as session:
                    # 1. Plain HTTP request to 18091
                    async with session.get("http://127.0.0.1:18091/audio/cfg/ping") as resp:
                        self.assertEqual(resp.status, 200)
                        data = await resp.json()
                        self.assertEqual(data["ping_result"]["status"], "ok")

                    # 2. HTTPS request to same port 18091
                    async with session.get("https://127.0.0.1:18091/audio/cfg/ping", ssl=client_ssl) as resp:
                        self.assertEqual(resp.status, 200)
                        data = await resp.json()
                        self.assertEqual(data["ping_result"]["status"], "ok")

                    # 3. Plain WebSocket to msHttp (18095)
                    async with session.ws_connect("http://127.0.0.1:18095", protocols=("miniserver",)) as ws:
                        greeting = await ws.receive_str()
                        self.assertIn("MINISERVER", greeting)

                    # 4. Secure WebSocket (WSS) to same msHttp (18095)
                    async with session.ws_connect("https://127.0.0.1:18095", protocols=("miniserver",), ssl=client_ssl) as ws:
                        greeting = await ws.receive_str()
                        self.assertIn("MINISERVER", greeting)

                await server.stop()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
