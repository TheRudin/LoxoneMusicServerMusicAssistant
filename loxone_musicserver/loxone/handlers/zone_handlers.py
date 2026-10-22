"""Loxone zone playback and control command handlers."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, Match, Optional

from ..responses import CommandResult, build_empty_response, build_response
from ..websocket import LoxoneZoneState

logger = logging.getLogger("loxone.handlers.zone")


class ZoneHandlers:
    """Handles zone commands like play, pause, volume, status, equalizer."""

    def __init__(
        self,
        get_zone_state: Callable[[int], Optional[LoxoneZoneState]],
        send_player_command: Callable[[int, str, Any], Awaitable[None]],
    ) -> None:
        self.get_zone_state = get_zone_state
        self.send_player_command = send_player_command

    async def get_status(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns the current state array for a zone."""
        zone_id = self._extract_zone_id(command, match)
        state = self.get_zone_state(zone_id)
        return build_response(command, "status", [state.to_dict()] if state else [])

    async def dynamic_command(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Handles dynamic playback commands: on, off, play, pause, resume, volume, position, queueplus, queueminus, repeat, shuffle."""
        parts = command.strip("/").split("/")
        # URL structure: audio/[cfg/]<zoneId>/<action>[/<param>]
        idx = 1
        if parts[0] == "audio" and len(parts) > 1 and parts[1] == "cfg":
            idx = 2

        if len(parts) <= idx:
            return build_empty_response(command)

        try:
            zone_id = int(parts[idx])
        except ValueError:
            return build_empty_response(command)

        action = parts[idx + 1].lower() if len(parts) > idx + 1 else "play"
        param = parts[idx + 2] if len(parts) > idx + 2 else None

        logger.debug("Zone command: zone=%d action=%s param=%s", zone_id, action, param)

        if action in ("play", "resume"):
            await self.send_player_command(zone_id, "play", None)
        elif action == "pause":
            await self.send_player_command(zone_id, "pause", None)
        elif action in ("stop", "off"):
            await self.send_player_command(zone_id, "stop", None)
        elif action == "on":
            await self.send_player_command(zone_id, "power_on", None)
        elif action == "volume":
            vol_val = self._parse_volume(zone_id, param)
            if vol_val is not None:
                await self.send_player_command(zone_id, "volume", vol_val)
        elif action == "position":
            try:
                pos = int(param) if param else 0
                await self.send_player_command(zone_id, "seek", pos)
            except ValueError:
                pass
        elif action == "queueplus":
            await self.send_player_command(zone_id, "next", None)
            return build_response(command, "queueplus", {"action": "queueplus"})
        elif action == "queueminus":
            await self.send_player_command(zone_id, "previous", None)
            return build_response(command, "queueminus", {"action": "queueminus"})
        elif action == "repeat":
            repeat_mode = int(param) if param and param.isdigit() else 1
            await self.send_player_command(zone_id, "repeat", repeat_mode)
        elif action == "shuffle":
            shuffle_val = param in ("1", "on", "enable", "true") if param else True
            await self.send_player_command(zone_id, "shuffle", shuffle_val)

        return build_empty_response(command)

    async def get_eq(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns equalizer bands for zone."""
        return build_response(command, "geteq", [])

    async def equalizer(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Sets or gets equalizer settings."""
        return build_empty_response(command)

    async def get_recent(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns recently played tracks for zone."""
        return build_response(command, "recent", [])

    def _extract_zone_id(self, command: str, match: Optional[Match[str]]) -> int:
        if match and "zone" in match.groupdict():
            return int(match.group("zone"))
        parts = command.strip("/").split("/")
        idx = 2 if len(parts) > 1 and parts[1] == "cfg" else 1
        try:
            return int(parts[idx])
        except (IndexError, ValueError):
            return 1

    def _parse_volume(self, zone_id: int, param: Optional[str]) -> Optional[int]:
        if not param:
            return None
        state = self.get_zone_state(zone_id)
        current_vol = state.volume if state else 30

        if param.startswith("+") or param.startswith("-"):
            try:
                delta = int(param)
                return max(0, min(100, current_vol + delta))
            except ValueError:
                return None
        try:
            val = int(param)
            return max(0, min(100, val))
        except ValueError:
            return None
