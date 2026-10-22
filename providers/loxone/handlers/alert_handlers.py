"""Loxone TTS and audio event/alert handlers."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, List, Match, Optional
from urllib.parse import unquote

from ..responses import CommandResult, build_response

logger = logging.getLogger("loxone.handlers.alerts")


class AlertHandlers:
    """Handles TTS announcements, alarms, bells, and audio alerts."""

    def __init__(
        self,
        play_tts: Callable[[List[int], str, Optional[str], Optional[int]], Awaitable[None]],
        play_alert: Callable[[List[int], str, Optional[int]], Awaitable[None]],
    ) -> None:
        self.play_tts = play_tts
        self.play_alert = play_alert

    async def zone_tts(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Plays a TTS message to a single zone: audio/<zone>/tts/<text>/<vol>."""
        parts = command.strip("/").split("/")
        idx = 2 if len(parts) > 1 and parts[1] == "cfg" else 1
        zone_id = int(parts[idx]) if len(parts) > idx and parts[idx].isdigit() else 1
        raw_text = parts[idx + 2] if len(parts) > idx + 2 else ""
        vol_str = parts[idx + 3] if len(parts) > idx + 3 else "50"
        vol = int(vol_str) if vol_str.isdigit() else 50

        decoded = unquote(raw_text).replace("+", " ")
        lang = None
        text = decoded
        if "|" in decoded:
            parts_tts = decoded.split("|", 1)
            lang = parts_tts[0].strip().lower()
            text = parts_tts[1].strip()

        logger.info("Playing TTS for zone %d: lang=%s text='%s' vol=%d", zone_id, lang, text, vol)
        await self.play_tts([zone_id], text, lang, vol)
        return build_response(command, "tts", {"success": True, "zone": zone_id})

    async def grouped_alert(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Handles grouped alerts and TTS: audio/grouped/<type>/[off|stop/]<zones>[/<param>]."""
        parts = command.strip("/").split("/")
        alert_type = parts[2].lower() if len(parts) > 2 else ""

        has_off = "off" in parts or "stop" in parts
        off_idx = -1
        if "off" in parts:
            off_idx = parts.index("off")
        elif "stop" in parts:
            off_idx = parts.index("stop")

        zones_idx = off_idx + 1 if has_off else 3
        zones_str = parts[zones_idx] if len(parts) > zones_idx else ""
        zones: List[int] = []
        for s in zones_str.split(","):
            if s.strip().isdigit():
                zones.append(int(s.strip()))

        if not zones:
            return build_response(command, "groupalert", [{"success": False, "reason": "no-zones"}])

        if alert_type == "tts":
            raw_text = "/".join(parts[zones_idx + 1 :]) if len(parts) > zones_idx + 1 else ""
            decoded = unquote(raw_text).replace("+", " ")
            lang = None
            text = decoded
            if "|" in decoded:
                p = decoded.split("|", 1)
                lang = p[0].strip().lower()
                text = p[1].strip()

            await self.play_tts(zones, text, lang, None)
            return build_response(command, "groupalert", [{"success": True, "type": "tts", "zones": zones}])

        # Other sound effects: bell, alarm, firealarm, clock, etc.
        await self.play_alert(zones, alert_type, None)
        return build_response(command, "groupalert", [{"success": True, "type": alert_type, "zones": zones}])

    async def zone_alert(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Handles zone sound effects: audio/<zone>/bell, alarm, firealarm, etc."""
        parts = command.strip("/").split("/")
        idx = 2 if len(parts) > 1 and parts[1] == "cfg" else 1
        zone_id = int(parts[idx]) if len(parts) > idx and parts[idx].isdigit() else 1
        alert_type = parts[idx + 1].lower() if len(parts) > idx + 1 else "bell"

        await self.play_alert([zone_id], alert_type, None)
        return build_response(command, alert_type, {"success": True, "zone": zone_id})
