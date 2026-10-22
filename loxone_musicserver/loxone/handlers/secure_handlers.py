"""Loxone secure/* bootstrap endpoints."""

from __future__ import annotations

import logging
from typing import Match, Optional

from ..responses import CommandResult, build_response

logger = logging.getLogger("loxone.handlers.secure")

LEGACY_JWT = (
    "eyJhbGciOiJSUzI1NiIsInR5cCI6IkpXVCJ9."
    "ewogICJleHAiOiAxNjQwNjEwMTQ0LAogICJpYXQiOiAxNjQwNjEwMDg0LAogICJzZXNzaW9uX3Rva2VuIjogIjhXYWh3QWZVTHdFUWNlOVl1MHFJRTlMN1FNa1hGSGJpME05Y2g5dktjZ1lBclBQb2pYSHBTaU5jcTBmVDNscUwiLAogICJzdWIiOiAic2VjdXJlLXNlc3Npb24taW5pdC1zdWNjZXNzIgp9."
    "Zd5M55YPirdugqlGr7u6iB-kM_oFqnvMnpxL8gj58vF2L4ocpSY6S8OB_4f8LeIB2AIYikN5U6R0UALJ3Oahxa0gq9qKDoNrjC7-Q8wAe1rEhDbvdWtaRzmgiHnivrz0cNsyeYGBX8c5Ix6pLI8URGjR1Ox2lbxBt_pVZ-MyEvhVNSJ0-DttclqIAgr_24tVmwe6lleT5eKyBoQVAcGJP-3LSdORKckHTCRw6aaf6sOQ7AtK37SXgnHB6J4g2wErvyw29mMAmDTbR8vZUCmTxgnmhbrks02AZITLaDeGAYTlSASWDSl84L9wkWOWk0pufZIGG0zcXgL8EoWD8cw_fIhbh-LXODEY5251u0DlVtaI_6J6o2j8jy_WvsSqKh-sqqy-ygScwPkLgFua7GNlppaHUGsFaEg0rVdLvVAiIV3mbOGnis1RuWcTWY9iuPVxFTODxkOZNRgZttBb_NFa8lQPJKwwhA33YC1hJ6DE3xEC2rvc4LGE400nLKnELNKpFNsom07JFSQQq8NV3Z1lzTksa8ANdXrV080J8x0c1Bt4dcUyx3lzFE8XG3DsLXCnL2YsJ9ik2jdSBZL8grnoQjqvJWaX3j47P0VM-jaMICVb6QcVP-nNB7k5n1qQGASsbkhcB1nffzE_wLooUe4iLxJQ2dkCM1n7ngXDF6HK0_A"
)


class SecureHandlers:
    """Implements secure/* endpoints for Loxone pairing and session setup."""

    def __init__(self, master_serial: str = "", config: Optional[Any] = None) -> None:
        self.config = config
        self.master_serial = master_serial.replace(":", "").replace("-", "").strip().upper()

    def set_master(self, serial: str) -> None:
        """Sets the paired Miniserver master serial."""
        clean = serial.replace(":", "").replace("-", "").strip().upper()
        self.master_serial = clean
        if self.config:
            self.config.master_serial = clean
        logger.info("Pairing updated: master Miniserver is '%s'", clean)

    @property
    def clean_master(self) -> str:
        if self.config and getattr(self.config, "master_serial", None):
            return getattr(self.config, "clean_master_serial", "").replace(":", "").replace("-", "").strip().upper()
        return self.master_serial.replace(":", "").replace("-", "").strip().upper()

    async def hello(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        clean = command.strip("/")
        remainder = clean[len("secure/hello") :].lstrip("/")
        if "/" in remainder:
            first_part, rest = remainder.split("/", 1)
            public_key = rest if first_part.isdigit() else remainder
        else:
            public_key = remainder

        return build_response(
            command,
            "secure_hello",
            {
                "command": "secure/hello",
                "error": 0,
                "public_key": public_key,
            },
            raw=True,
        )

    async def info_pairing(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        is_paired = bool(self.clean_master)
        return build_response(
            command,
            "secure_infopairing",
            {
                "command": "secure/infopairing",
                "error": 0 if is_paired else -81,
                "master": self.clean_master,
                "peers": [],
            },
            raw=True,
        )

    async def authenticate(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        return build_response(
            command,
            "authenticate",
            {
                "command": "secure/authenticate",
                "response": "authentication successful",
            },
            raw=True,
        )

    async def init(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        return build_response(
            command,
            "secure_init",
            {
                "command": "secure/init",
                "error": 0,
                "jwt": LEGACY_JWT,
            },
            raw=True,
        )
