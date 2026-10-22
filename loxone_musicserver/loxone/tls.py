"""TLS certificate management and context creation for Loxone servers."""

from __future__ import annotations

import datetime
import ipaddress
import logging
import os
from pathlib import Path
import ssl
import subprocess
from typing import Optional

logger = logging.getLogger("loxone.tls")


def get_or_create_tls_context(data_dir: Path) -> Optional[ssl.SSLContext]:
    """Loads or generates a self-signed TLS certificate for dual-protocol server."""
    data_dir.mkdir(parents=True, exist_ok=True)
    cert_path = data_dir / "loxone_cert.pem"
    key_path = data_dir / "loxone_key.pem"

    if not cert_path.exists() or not key_path.exists():
        logger.info("Generating self-signed TLS certificate for Loxone Audioserver...")
        success = _generate_certificate(cert_path, key_path)
        if not success:
            logger.warning("Could not generate self-signed TLS certificate; TLS will be disabled")
            return None

    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(cert_path), str(key_path))
        logger.info("Loaded TLS certificate from %s", cert_path)
        return ctx
    except Exception as exc:
        logger.warning("Failed to load TLS certificate chain: %s", exc)
        return None


def _generate_certificate(cert_path: Path, key_path: Path) -> bool:
    """Generates a self-signed certificate via cryptography or openssl CLI."""
    # 1. Try cryptography library
    try:
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COMMON_NAME, "MusicAssistant-AudioServer"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Loxone"),
        ])
        now = datetime.datetime.now(datetime.timezone.utc)
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(
                x509.SubjectAlternativeName([
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
                ]),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )

        cert_bytes = cert.public_bytes(serialization.Encoding.PEM)
        key_bytes = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )

        cert_path.write_bytes(cert_bytes)
        key_path.write_bytes(key_bytes)
        os.chmod(key_path, 0o600)
        return True
    except ImportError:
        pass
    except Exception as exc:
        logger.warning("Cryptography generation failed: %s; falling back to openssl CLI", exc)

    # 2. Fallback to openssl CLI
    try:
        cmd = [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path),
            "-out", str(cert_path),
            "-days", "3650", "-nodes",
            "-subj", "/CN=MusicAssistant-AudioServer/O=Loxone"
        ]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        os.chmod(key_path, 0o600)
        return True
    except Exception as exc:
        logger.warning("OpenSSL CLI generation failed: %s", exc)
        return False
