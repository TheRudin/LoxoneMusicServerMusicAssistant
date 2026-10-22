"""RSA Crypto and TLS certificate generation for Loxone emulation."""

from __future__ import annotations

import datetime
import ipaddress
from typing import Tuple

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID


class LoxoneCrypto:
    """Manages RSA keys and TLS certificates for Loxone communication."""

    def __init__(self) -> None:
        # Generate 2048-bit RSA key pair for Loxone audio/cfg/getkey
        self.private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        self.public_key = self.private_key.public_key()

        # Compute modulus hex and exponent
        public_numbers = self.public_key.public_numbers()
        self.modulus_hex = f"{public_numbers.n:x}"
        self.exponent = public_numbers.e

    def export_public_key_pem(self) -> str:
        """Exports the public key in SPKI PEM format."""
        pem = self.public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return pem.decode("utf-8")

    def decrypt(self, ciphertext: bytes) -> bytes:
        """Decrypts payload encrypted with the public key."""
        try:
            return self.private_key.decrypt(
                ciphertext,
                padding.PKCS1v15(),
            )
        except Exception:
            # Fallback to OAEP
            return self.private_key.decrypt(
                ciphertext,
                padding.OAEP(
                    mgf=padding.MGF1(algorithm=hashes.SHA1()),
                    algorithm=hashes.SHA1(),
                    label=None,
                ),
            )

    @classmethod
    def generate_self_signed_cert(cls) -> Tuple[bytes, bytes]:
        """Generates a self-signed certificate and private key for TLS listeners."""
        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        subject = issuer = x509.Name(
            [
                x509.NameAttribute(NameOID.COMMON_NAME, "audioserver.local"),
                x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Loxone Music Server Emulator"),
            ]
        )
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1))
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("audioserver.local"),
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                    ]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )

        cert_pem = cert.public_bytes(serialization.Encoding.PEM)
        key_pem = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
        return cert_pem, key_pem
