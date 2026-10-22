"""Loxone audio/cfg/* configuration handlers."""

from __future__ import annotations

import base64
import binascii
import json
import logging
import time
from typing import Any, Callable, Dict, List, Match, Optional
from urllib.parse import unquote

from ..codec import decode_loxone_id
from ..crypto import LoxoneCrypto
from ..responses import CommandResult, build_empty_response, build_response
from ..config import ServerConfig

logger = logging.getLogger("loxone.handlers.config")


class ConfigHandlers:
    """Implements audio/cfg/* endpoints."""

    def __init__(
        self,
        config: ServerConfig,
        crypto: LoxoneCrypto,
        on_config_updated: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> None:
        self.config = config
        self.crypto = crypto
        self.on_config_updated = on_config_updated
        self.current_crc32: Optional[str] = None
        self.extensions: List[Any] = []
        self.raw_config: Dict[str, Any] = {}

    async def get_key(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns public key modulus and exponent for Loxone Config."""
        return build_response(
            command,
            "getkey",
            [{"pubkey": self.crypto.modulus_hex, "exp": self.crypto.exponent}],
        )

    async def get_key_full(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns public key in SPKI PEM format."""
        return build_response(
            command,
            "getkey",
            [{"pubkey": self.crypto.export_public_key_pem()}],
        )

    async def ready(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Handshake readiness query."""
        return build_response(
            command,
            "ready",
            {"session": int(time.time() * 1000)},
        )

    async def miniserver_time(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns server time in milliseconds."""
        return build_response(
            command,
            "miniservertime",
            int(time.time() * 1000),
        )

    async def identify(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Identifies server by MAC and active services."""
        return build_response(
            command,
            "identify",
            {"mac": self.config.clean_mac, "services": []},
        )

    async def get_config(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns current configuration CRC32 and extensions."""
        return build_response(
            command,
            "getconfig",
            {"crc32": self.current_crc32, "extensions": self.extensions},
        )

    async def set_config(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Saves configuration deployed by Loxone Config."""
        parts = command.split("/")
        # URL-encoded base64 payload is in parts[3]
        encoded = parts[3] if len(parts) > 3 else ""
        if not encoded and payload:
            raw_bytes = payload
        else:
            try:
                # Add base64 padding
                norm = unquote(encoded)
                padding = 4 - (len(norm) % 4)
                if padding and padding != 4:
                    norm += "=" * padding
                raw_bytes = base64.b64decode(norm)
            except Exception as exc:
                logger.warning("Failed to decode setconfig payload: %s", exc)
                raw_bytes = b""

        if raw_bytes:
            crc = f"{binascii.crc32(raw_bytes) & 0xffffffff:08x}"
            self.current_crc32 = crc
            try:
                parsed = json.loads(raw_bytes.decode("utf-8"))
                self.raw_config = parsed
                if self.on_config_updated:
                    self.on_config_updated(parsed)
                logger.info("Saved new Loxone config (CRC32: %s)", crc)
            except Exception as exc:
                logger.warning("Error parsing setconfig JSON: %s", exc)

        return build_response(
            command,
            "setconfig",
            {"crc32": self.current_crc32, "extensions": self.extensions},
        )

    async def set_config_timestamp(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Acknowledges config timestamp."""
        parts = command.split("/")
        ts = parts[3] if len(parts) > 3 else str(int(time.time()))
        return build_response(
            command,
            "setconfigtimestamp",
            {"success": True, "timestamp": int(ts) if ts.isdigit() else ts, "crc32": self.current_crc32},
        )

    async def diagnosis(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns diagnostics data."""
        return build_response(
            command,
            "diagnosis",
            {
                "server": self.config.server_name,
                "version": self.config.firmware_version,
                "mac": self.config.formatted_mac,
                "uptime": int(time.time()),
            },
        )

    async def restart(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Acknowledge restart command."""
        logger.info("Loxone requested restart")
        return build_response(command, "restart", True)
