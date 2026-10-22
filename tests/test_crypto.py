"""Unit tests for Loxone Crypto and RSA."""

import unittest
from loxone_musicserver.loxone.crypto import LoxoneCrypto
from cryptography.hazmat.primitives.asymmetric import padding


class TestLoxoneCrypto(unittest.TestCase):
    def test_rsa_key_generation(self):
        crypto = LoxoneCrypto()
        self.assertIsNotNone(crypto.modulus_hex)
        self.assertEqual(crypto.exponent, 65537)
        self.assertTrue(len(crypto.modulus_hex) > 100)

        pem = crypto.export_public_key_pem()
        self.assertIn("BEGIN PUBLIC KEY", pem)
        self.assertIn("END PUBLIC KEY", pem)

    def test_encrypt_and_decrypt(self):
        crypto = LoxoneCrypto()
        secret = b"MySuperSecretToken123"

        ciphertext = crypto.public_key.encrypt(
            secret,
            padding.PKCS1v15(),
        )
        decrypted = crypto.decrypt(ciphertext)
        self.assertEqual(decrypted, secret)

    def test_tls_cert_generation(self):
        cert_pem, key_pem = LoxoneCrypto.generate_self_signed_cert()
        self.assertIn(b"BEGIN CERTIFICATE", cert_pem)
        self.assertIn(b"BEGIN RSA PRIVATE KEY", key_pem)


if __name__ == "__main__":
    unittest.main()
