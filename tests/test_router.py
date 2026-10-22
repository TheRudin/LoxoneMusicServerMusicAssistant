"""Unit tests for Loxone Router."""

import asyncio
import re
import unittest
from loxone_musicserver.loxone.responses import build_response
from loxone_musicserver.loxone.router import LoxoneRouter


class TestLoxoneRouter(unittest.TestCase):
    def test_prefix_dispatch(self):
        router = LoxoneRouter()

        async def ready_handler(command, payload, match):
            return build_response(command, "ready", {"ok": True})

        router.register_prefix("audio/cfg/ready", ready_handler)

        async def run():
            res = await router.dispatch("audio/cfg/ready")
            self.assertEqual(res.name, "ready")
            self.assertEqual(res.payload, {"ok": True})

        asyncio.run(run())

    def test_regex_dispatch(self):
        router = LoxoneRouter()

        captured = {}

        async def zone_handler(command, payload, match):
            captured["zone"] = match.group("zone")
            captured["cmd"] = match.group("cmd")
            return build_response(command, captured["cmd"], "done")

        pattern = re.compile(r"^audio/(?P<zone>\d+)/(?P<cmd>play|pause)")
        router.register_regex(pattern, zone_handler)

        async def run():
            res = await router.dispatch("audio/2/play")
            self.assertEqual(res.name, "play")
            self.assertEqual(captured["zone"], "2")
            self.assertEqual(captured["cmd"], "play")

        asyncio.run(run())

    def test_unhandled_route(self):
        router = LoxoneRouter()

        async def run():
            res = await router.dispatch("unknown/path")
            self.assertEqual(res.payload, [])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
