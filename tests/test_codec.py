"""Unit tests for Loxone ID Codec."""

import unittest
from loxone_musicserver.loxone.codec import (
    BASE_PLAYLIST,
    BASE_FAVORITE_ZONE,
    encode_loxone_id,
    decode_loxone_id,
)


class TestLoxoneIdCodec(unittest.TestCase):
    def test_encode_and_decode(self):
        data = "library://track/42"
        offset = BASE_FAVORITE_ZONE + 1
        encoded = encode_loxone_id(data, offset)

        self.assertIsInstance(encoded, str)
        self.assertTrue(len(encoded) > 0)
        # Verify URL-safe (no +, /, or =)
        self.assertNotIn("+", encoded)
        self.assertNotIn("/", encoded)
        self.assertNotIn("=", encoded)

        decoded = decode_loxone_id(encoded)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded[0], data)
        self.assertEqual(decoded[1], offset)

    def test_numeric_data(self):
        encoded = encode_loxone_id(1234, BASE_PLAYLIST)
        decoded = decode_loxone_id(encoded)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded[0], 1234)
        self.assertEqual(decoded[1], BASE_PLAYLIST)

    def test_invalid_decode(self):
        self.assertIsNone(decode_loxone_id(""))
        self.assertIsNone(decode_loxone_id("not-valid-base64!@#$"))


if __name__ == "__main__":
    unittest.main()
