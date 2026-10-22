"""Configuration management for Loxone Music Server emulator."""

from __future__ import annotations

import os
from typing import Optional
import uuid
from dataclasses import dataclass, field
from pathlib import Path


def get_default_mac() -> str:
    """Returns a stable MAC-like address."""
    node = uuid.getnode()
    mac = ":".join(f"{(node >> i) & 0xff:02X}" for i in range(40, -1, -8))
    # Ensure locally administered unicast bit
    return mac


@dataclass
class ServerConfig:
    """Configuration settings."""

    # Server identification
    server_name: str = "MusicAssistant-AudioServer"
    mac_address: str = field(default_factory=get_default_mac)
    firmware_version_number: str = "17.2.08.28"
    mdns_version: str = "17.02.08.28"
    api_version: str = "~API:1.6~"
    session_token: str = "8WahwAfULwEQce9Yu0qIE9L7QMkXFHbi0M9ch9vKcgYArPPojXHpSiNcq0fT3lqL"
    master_serial: str = ""
    uuid: str = ""
    miniserver_ip: str = field(default_factory=lambda: os.environ.get("MINISERVER_IP", "192.168.1.107"))
    miniserver_port: int = 80
    last_startup_ping: Optional[dict] = None

    # Network ports
    host: str = "0.0.0.0"
    app_port: int = 7091
    ms_port: int = 7095
    discovery_port: int = 7070

    # Music Assistant connection
    ma_url: str = field(default_factory=lambda: os.environ.get("MA_URL", "http://localhost:8095"))
    ma_username: str = field(default_factory=lambda: os.environ.get("MA_USERNAME", "admin"))
    ma_password: str = field(default_factory=lambda: os.environ.get("MA_PASSWORD", ""))
    ma_token: str = field(default_factory=lambda: os.environ.get("MA_TOKEN", ""))

    # Storage
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("LOXONE_DATA_DIR", "./data")))

    @property
    def firmware_version(self) -> str:
        return f"LWSS V {self.firmware_version_number}"

    @property
    def clean_mac(self) -> str:
        """Returns uppercase 12-char hex MAC without colons or dashes."""
        return self.mac_address.replace(":", "").replace("-", "").upper()

    @property
    def formatted_mac(self) -> str:
        raw = self.clean_mac
        if len(raw) == 12:
            return ":".join(raw[i : i + 2] for i in range(0, 12, 2))
        return self.mac_address

    def get_identification(self, server_name: str) -> str:
        """Returns the identification string sent upon WebSocket connect."""
        if server_name == "msHttp":
            base = f"MINISERVER V {self.firmware_version} {self.clean_mac}"
        else:
            base = self.firmware_version
        return f"{base} | {self.api_version} | Session-Token: {self.session_token}"
