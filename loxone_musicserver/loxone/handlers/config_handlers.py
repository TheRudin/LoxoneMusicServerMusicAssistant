"""Loxone audio/cfg/* configuration handlers."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Match, Optional
from urllib.parse import quote, unquote

import aiohttp

from ..codec import decode_loxone_id
from ..crypto import LoxoneCrypto
from ..responses import CommandResult, build_empty_response, build_response
from ...config import ServerConfig

logger = logging.getLogger("loxone.handlers.config")


class ConfigHandlers:
    """Implements audio/cfg/* endpoints."""

    def __init__(
        self,
        config: ServerConfig,
        crypto: LoxoneCrypto,
        on_config_updated: Optional[Callable[[Dict[str, Any]], None]] = None,
        notifier: Optional[Any] = None,
        on_player_name_updated: Optional[Callable[[int, str], None]] = None,
        get_extra_diagnostics: Optional[Callable[[], Any]] = None,
    ) -> None:
        self.config = config
        self.crypto = crypto
        self.on_config_updated = on_config_updated
        self.notifier = notifier
        self.on_player_name_updated = on_player_name_updated
        self.get_extra_diagnostics = get_extra_diagnostics
        self.current_crc32: Optional[str] = None
        self.extensions: List[Any] = []
        self.raw_config: Dict[str, Any] = {}
        self._load_state()

    def _find_server_section(self, parsed: Any) -> Optional[Dict[str, Any]]:
        """Finds the server configuration section matching clean_mac."""
        mac_needle = self.config.clean_mac
        if isinstance(parsed, list):
            for entry in parsed:
                if isinstance(entry, dict):
                    for k, v in entry.items():
                        if k.replace(":", "").replace("-", "").upper() == mac_needle and isinstance(v, dict):
                            return v
        elif isinstance(parsed, dict):
            for k, v in parsed.items():
                if k.replace(":", "").replace("-", "").upper() == mac_needle and isinstance(v, dict):
                    return v
        return None

    def _save_state(self) -> None:
        """Persists Loxone state and pairing data to disk."""
        try:
            state_dir = self.config.data_dir
            state_dir.mkdir(parents=True, exist_ok=True)
            state_file = state_dir / "loxone_state.json"
            data = {
                "uuid": self.config.uuid,
                "miniserver_ip": self.config.miniserver_ip,
                "master_serial": self.config.clean_master_serial,
                "mac_address": self.config.formatted_mac,
                "crc32": self.current_crc32,
                "server_name": self.config.server_name,
                "last_startup_ping": self.config.last_startup_ping,
            }
            with open(state_file, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.debug("Saved Loxone state to %s", state_file)
        except Exception as exc:
            logger.debug("Failed to save loxone_state.json: %s", exc)

    def _load_state(self) -> None:
        """Loads persisted Loxone state and pairing data from disk."""
        try:
            state_file = self.config.data_dir / "loxone_state.json"
            if state_file.exists():
                with open(state_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if not self.config.uuid and data.get("uuid"):
                    self.config.uuid = str(data["uuid"])
                if (not self.config.miniserver_ip or self.config.miniserver_ip == "192.168.1.107") and data.get("miniserver_ip"):
                    self.config.miniserver_ip = str(data["miniserver_ip"])
                if not self.config.master_serial and data.get("master_serial"):
                    self.config.master_serial = str(data["master_serial"]).replace(":", "").replace("-", "").strip().upper()
                if data.get("mac_address"):
                    self.config.mac_address = str(data["mac_address"]).strip()
                if not self.current_crc32 and data.get("crc32"):
                    self.current_crc32 = str(data["crc32"])
                if data.get("last_startup_ping"):
                    self.config.last_startup_ping = data["last_startup_ping"]
                logger.info(
                    "Loaded persisted Loxone state: uuid=%s, ms_ip=%s, master=%s, mac=%s",
                    self.config.uuid,
                    self.config.miniserver_ip,
                    self.config.clean_master_serial,
                    self.config.mac_address,
                )
        except Exception as exc:
            logger.debug("Failed to load loxone_state.json: %s", exc)

    async def notify_miniserver_startup(
        self,
        miniserver_ip: Optional[str] = None,
        uuid: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Pings Miniserver devicestartup endpoint to transition Audioserver to Online (Grün)."""
        target_ip = (miniserver_ip or self.config.miniserver_ip or "192.168.1.107").strip()
        if "." not in target_ip:
            target_ip = "192.168.1.107"
        target_uuid = (uuid or self.config.uuid).strip()
        port = self.config.miniserver_port or 80

        if not target_ip or not target_uuid:
            logger.debug(
                "Skipping devicestartup ping (target_ip=%s, target_uuid=%s)",
                target_ip,
                target_uuid,
            )
            result = {
                "success": False,
                "error": "missing-ip-or-uuid",
                "ip": target_ip,
                "uuid": target_uuid,
            }
            self.config.last_startup_ping = result
            return result

        url = f"http://{target_ip}:{port}/dev/sps/devicestartup/{quote(target_uuid)}"
        ts = int(time.time())
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=4.0)) as resp:
                    text = await resp.text()
                    is_ok = resp.status == 200
                    logger.info(
                        "Miniserver devicestartup ping to %s returned %s: %s",
                        url,
                        resp.status,
                        text.strip(),
                    )
                    result = {
                        "success": is_ok,
                        "timestamp": ts,
                        "url": url,
                        "status": resp.status,
                        "response": text.strip(),
                    }
                    self.config.last_startup_ping = result
                    self._save_state()
                    return result
        except Exception as exc:
            logger.warning("Miniserver devicestartup ping to %s failed: %s", url, exc)
            fallback_ip = "192.168.1.107"
            if target_ip != fallback_ip:
                fallback_url = f"http://{fallback_ip}:{port}/dev/sps/devicestartup/{quote(target_uuid)}"
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(fallback_url, timeout=aiohttp.ClientTimeout(total=3.0)) as resp:
                            text = await resp.text()
                            logger.info("Miniserver fallback ping to %s returned %s: %s", fallback_url, resp.status, text.strip())
                            self.config.miniserver_ip = fallback_ip
                            result = {
                                "success": True,
                                "timestamp": ts,
                                "url": fallback_url,
                                "status": resp.status,
                                "response": text.strip(),
                            }
                            self.config.last_startup_ping = result
                            self._save_state()
                            return result
                except Exception as fb_exc:
                    logger.debug("Fallback ping to %s failed: %s", fallback_url, fb_exc)

            result = {
                "success": False,
                "timestamp": ts,
                "url": url,
                "error": str(exc),
            }
            self.config.last_startup_ping = result
            self._save_state()
            return result

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

                # Extract and store device section matching this Audioserver MAC
                section = self._find_server_section(parsed)
                if section:
                    uuid_val = section.get("uuid")
                    if uuid_val:
                        self.config.uuid = str(uuid_val).strip()

                    ip_val = section.get("ip") or section.get("host_ip")
                    if ip_val and "." in str(ip_val):
                        self.config.miniserver_ip = str(ip_val).strip()
                    elif ip_val:
                        logger.info("Ignoring non-IP hostname '%s' from setconfig (keeping %s)", ip_val, self.config.miniserver_ip)

                    master_val = section.get("master")
                    if master_val:
                        clean_master = str(master_val).replace(":", "").replace("-", "").upper()
                        self.config.master_serial = clean_master

                    name_val = section.get("name")
                    if name_val:
                        self.config.server_name = str(name_val).strip()

                    logger.info(
                        "Extracted Audioserver section from setconfig: uuid=%s, ms_ip=%s, master=%s, name=%s",
                        self.config.uuid,
                        self.config.miniserver_ip,
                        self.config.master_serial,
                        self.config.server_name,
                    )
                    self._save_state()

                    if self.config.uuid:
                        asyncio.create_task(self.notify_miniserver_startup())

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
        ws_info = self.notifier.get_connection_info() if self.notifier else []
        recent_cmds = self.notifier.get_recent_commands() if self.notifier else []
        diag_data: Dict[str, Any] = {
            "server": self.config.server_name,
            "version": self.config.firmware_version,
            "mac": self.config.formatted_mac,
            "clean_mac": self.config.clean_mac,
            "uptime": int(time.time()),
            "paired": bool(self.config.master_serial),
            "master_serial": self.config.clean_master_serial,
            "miniserver_ip": self.config.miniserver_ip,
            "uuid": self.config.uuid,
            "last_startup_ping": self.config.last_startup_ping,
            "ws_connections_count": len(ws_info),
            "ws_connections": ws_info,
            "recent_commands": recent_cmds,
        }
        if self.get_extra_diagnostics:
            try:
                extra = self.get_extra_diagnostics()
                if asyncio.iscoroutine(extra):
                    extra = await extra
                if isinstance(extra, dict):
                    diag_data.update(extra)
            except Exception as exc:
                diag_data["extra_diag_error"] = str(exc)

        return build_response(command, "diagnosis", diag_data)

    async def restart(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Handles restart command from Miniserver."""
        logger.info("Restart requested by Miniserver")
        if self.notifier:
            try:
                await self.notifier.broadcast_message(json.dumps({"restart_event": []}))
            except Exception as exc:
                logger.debug("Failed to broadcast restart_event: %s", exc)

        async def _delayed_ping() -> None:
            await asyncio.sleep(1.0)
            await self.notify_miniserver_startup()

        asyncio.create_task(_delayed_ping())
        return build_response(command, "restart", True)

    async def trigger_startup(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Manually trigger devicestartup ping to Miniserver."""
        result = await self.notify_miniserver_startup()
        return build_response(command, "devicestartup", result)

    async def player_name(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Handles player / zone renaming from Loxone Config or Miniserver."""
        parts = command.split("/")
        encoded = parts[3] if len(parts) > 3 else ""
        raw_bytes = b""
        if not encoded and payload:
            raw_bytes = payload
        elif encoded:
            try:
                norm = unquote(encoded)
                padding = 4 - (len(norm) % 4)
                if padding and padding != 4:
                    norm += "=" * padding
                raw_bytes = base64.b64decode(norm)
            except Exception as exc:
                logger.warning("Failed to decode playername payload: %s", exc)

        if raw_bytes:
            try:
                parsed = json.loads(raw_bytes.decode("utf-8"))
                logger.info("Received audio/cfg/playername payload: %s", parsed)
                items = parsed if isinstance(parsed, list) else [parsed]
                for item in items:
                    if isinstance(item, dict):
                        zone_id = item.get("playerid") or item.get("player") or item.get("id") or item.get("zone")
                        name = item.get("name")
                        if zone_id is not None and name and self.on_player_name_updated:
                            try:
                                self.on_player_name_updated(int(zone_id), str(name))
                            except Exception as e:
                                logger.warning("Failed to update player name for zone %s: %s", zone_id, e)
            except Exception as exc:
                logger.warning("Error parsing playername JSON: %s", exc)

        return build_response(command, "playername", {"success": True, "result": ""})

    async def volumes(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Handles volume configuration queries and adjustments."""
        return build_response(command, "volumes", {"success": True})

    async def get_inputs(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns available line inputs (empty list for virtual Audioserver)."""
        return build_response(command, "getinputs", [])

    async def get_all(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns general audio configuration."""
        return build_response(command, "all", {"success": True})
