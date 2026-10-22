"""Loxone queue management command handlers."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Match, Optional

from ..responses import CommandResult, build_empty_response, build_response

logger = logging.getLogger("loxone.handlers.queue")


class QueueHandlers:
    """Handles queue retrieval, item selection, insertion and clearing."""

    def __init__(
        self,
        get_queue_items: Callable[[int, int, int], Awaitable[Dict[str, Any]]],
        control_queue: Callable[[int, str, Any], Awaitable[None]],
    ) -> None:
        self.get_queue_items = get_queue_items
        self.control_queue = control_queue

    async def get_queue(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns the current queue of a zone."""
        parts = command.strip("/").split("/")
        idx = 2 if len(parts) > 1 and parts[1] == "cfg" else 1
        zone_id = int(parts[idx]) if len(parts) > idx and parts[idx].isdigit() else 1
        start = int(parts[idx + 2]) if len(parts) > idx + 2 and parts[idx + 2].isdigit() else 0
        limit = int(parts[idx + 3]) if len(parts) > idx + 3 and parts[idx + 3].isdigit() else 50

        queue_data = await self.get_queue_items(zone_id, start, limit)
        return build_response(command, "getqueue", [queue_data])

    async def queue_play(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Plays a specific item in the queue."""
        parts = command.strip("/").split("/")
        idx = 2 if len(parts) > 1 and parts[1] == "cfg" else 1
        zone_id = int(parts[idx]) if len(parts) > idx and parts[idx].isdigit() else 1
        item_id = "/".join(parts[idx + 3 :]) if len(parts) > idx + 3 else ""

        await self.control_queue(zone_id, "play_item", item_id)
        return build_empty_response(command)

    async def queue_clear(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Clears the queue of a zone."""
        parts = command.strip("/").split("/")
        idx = 2 if len(parts) > 1 and parts[1] == "cfg" else 1
        zone_id = int(parts[idx]) if len(parts) > idx and parts[idx].isdigit() else 1

        await self.control_queue(zone_id, "clear", None)
        return build_empty_response(command)

    async def queue_add(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Appends an item to the queue."""
        parts = command.strip("/").split("/")
        idx = 2 if len(parts) > 1 and parts[1] == "cfg" else 1
        zone_id = int(parts[idx]) if len(parts) > idx and parts[idx].isdigit() else 1
        media_id = "/".join(parts[idx + 2 :]) if len(parts) > idx + 2 else ""

        await self.control_queue(zone_id, "add", media_id)
        return build_empty_response(command)

    async def queue_and_play(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Adds an item and starts playing immediately."""
        parts = command.strip("/").split("/")
        idx = 2 if len(parts) > 1 and parts[1] == "cfg" else 1
        zone_id = int(parts[idx]) if len(parts) > idx and parts[idx].isdigit() else 1
        media_id = "/".join(parts[idx + 2 :]) if len(parts) > idx + 2 else ""

        await self.control_queue(zone_id, "play_media", media_id)
        return build_empty_response(command)
