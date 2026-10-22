"""Unit tests for Loxone responses serialization."""

import json
import unittest
from loxone_musicserver.loxone.responses import (
    build_response,
    build_empty_response,
    infer_result_name,
    serialize_result,
)


class TestLoxoneResponses(unittest.TestCase):
    def test_infer_result_name(self):
        self.assertEqual(infer_result_name("audio/cfg/getkey"), "getkey")
        self.assertEqual(infer_result_name("audio/1/play"), "play")
        self.assertEqual(infer_result_name("audio/cfg/getplaylists2"), "getplaylists2")
        self.assertEqual(infer_result_name("audio/1/volume/25"), "volume")

    def test_build_and_serialize(self):
        res = build_response("audio/cfg/ready", "ready", {"session": 12345})
        raw = serialize_result(res)
        parsed = json.loads(raw)

        self.assertIn("ready_result", parsed)
        self.assertEqual(parsed["ready_result"]["session"], 12345)
        self.assertEqual(parsed["command"], "audio/cfg/ready")

    def test_empty_response(self):
        res = build_empty_response("audio/1/unknown")
        raw = serialize_result(res)
        parsed = json.loads(raw)

        self.assertIn("unknown_result", parsed)
        self.assertEqual(parsed["unknown_result"], [])


if __name__ == "__main__":
    unittest.main()
