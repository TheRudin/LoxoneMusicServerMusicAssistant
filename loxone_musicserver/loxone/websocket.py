"""Loxone WebSocket notification, connection registry, and state broadcasting."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from aiohttp import web

from ..config import ServerConfig

logger = logging.getLogger("loxone.websocket")


@dataclass
class LoxoneZoneState:
    """Wire representation of an AudioServer zone state for Loxone clients."""

    playerid: int
    name: str
    power: str = "on"  # "on" | "off"
    mode: str = "stop"  # "play" | "pause" | "stop"
    clientState: str = "on"  # "on" | "off"
    time: int = 0
    duration: int = 0
    volume: int = 30
    title: str = ""
    artist: str = ""
    album: str = ""
    station: str = ""
    sourceName: str = ""
    coverurl: str = ""
    audiopath: str = ""
    audiotype: int = 0  # 0=File, 1=Radio, 2=Playlist, 3=LineIn, 4=AirPlay, 5=Spotify
    type: int = 2  # FileType.File
    plrepeat: int = 0  # 0=None, 1=Queue, 3=Track
    plshuffle: int = 0  # 0=Off, 1=On
    equalizerSettings: str = "0,0,0,0,0,0,0,0,0,0"
    syncedzones: List[int] = field(default_factory=list)
    mastervolume: int = 0
    qindex: int = 0
    parent: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not d["title"] and d["name"]:
            d["title"] = d["name"]
        return d


class LoxoneWebSocketNotifier:
    """Manages active WebSocket connections and broadcasts zone state updates."""

    def __init__(self, config: ServerConfig, get_all_states: Callable[[], List[LoxoneZoneState]]) -> None:
        self.config = config
        self.get_all_states = get_all_states
        self.connections: Set[Tuple[web.WebSocketResponse, str]] = set()
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._running = False

    async def start(self) -> None:
        """Starts the periodic heartbeat task."""
        self._running = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop(self) -> None:
        """Stops the heartbeat loop and closes all connections."""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        for ws, _ in list(self.connections):
            try:
                await ws.close()
            except Exception:
                pass
        self.connections.clear()

    async def handle_connection(self, request: web.Request, server_name: str) -> web.WebSocketResponse:
        """Upgrades HTTP connection to WebSocket and registers the client."""
        ws = web.WebSocketResponse(heartbeat=30.0)
        await ws.prepare(request)

        entry = (ws, server_name)
        self.connections.add(entry)
        logger.info("Loxone WebSocket connected on %s from %s (total: %d)", server_name, request.remote, len(self.connections))

        try:
            # 1. Send identification string
            greeting = self.config.get_identification(server_name)
            await ws.send_str(greeting)

            # 2. If msHttp, send heartbeat immediately
            if server_name == "msHttp":
                await ws.send_str(json.dumps({"server_heartbeat": {"time": int(time.time())}}))

            # 3. Send current state of all zones on connect
            states = [s.to_dict() for s in self.get_all_states()]
            if states:
                await ws.send_str(json.dumps({"audio_event": states}))

            # 4. Listen for incoming messages from client (if any)
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    logger.debug("Received WS text from %s: %s", server_name, msg.data)
                elif msg.type == web.WSMsgType.ERROR:
                    logger.warning("WS connection error on %s: %s", server_name, ws.exception())
        finally:
            self.connections.discard(entry)
            logger.info("Loxone WebSocket disconnected from %s (remaining: %d)", server_name, len(self.connections))

        return ws

    async def broadcast_message(self, message: str) -> None:
        """Broadcasts a raw JSON string to all open WebSocket connections."""
        for ws, srv in list(self.connections):
            try:
                await ws.send_str(message)
            except Exception as exc:
                logger.debug("Failed to send WS message to %s: %s", srv, exc)
                self.connections.discard((ws, srv))

    def notify_zone_state_changed(self, state: LoxoneZoneState) -> None:
        """Broadcasts an updated zone state to all clients."""
        payload = json.dumps({"audio_event": [state.to_dict()]})
        asyncio.create_task(self.broadcast_message(payload))

    def notify_queue_updated(self, zone_id: int, queue_size: int) -> None:
        """Broadcasts audio_queue_event."""
        payload = json.dumps(
            {
                "audio_queue_event": [
                    {
                        "playerid": zone_id,
                        "queuesize": queue_size,
                        "restrictions": 1,
                    }
                ]
            }
        )
        asyncio.create_task(self.broadcast_message(payload))

    def notify_room_favorites_changed(self, zone_id: int, count: int) -> None:
        """Broadcasts roomfavchanged_event."""
        payload = json.dumps(
            {
                "roomfavchanged_event": [
                    {
                        "playerid": zone_id,
                        "count": count,
                    }
                ]
            }
        )
        asyncio.create_task(self.broadcast_message(payload))

    async def _heartbeat_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(10.0)
                heartbeat = json.dumps({"server_heartbeat": {"time": int(time.time())}})
                # Send heartbeat specifically to msHttp sockets (or all)
                for ws, srv in list(self.connections):
                    if srv == "msHttp":
                        try:
                            await ws.send_str(heartbeat)
                        except Exception:
                            self.connections.discard((ws, srv))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Error in heartbeat loop: %s", exc)
