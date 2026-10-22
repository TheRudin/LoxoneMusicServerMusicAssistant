"""Loxone WebSocket notification, connection registry, and state broadcasting."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from aiohttp import web

from ..config import ServerConfig
from .responses import serialize_result

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

    def __init__(
        self,
        config: ServerConfig,
        get_all_states: Callable[[], List[LoxoneZoneState]],
        router: Optional[Any] = None,
    ) -> None:
        self.config = config
        self.get_all_states = get_all_states
        self.router = router
        self.connections: Set[Tuple[web.WebSocketResponse, str]] = set()
        self._heartbeat_task: Optional[asyncio.Task[None]] = None
        self._running = False
        self._start_time = time.time()

    def set_router(self, router: Any) -> None:
        """Sets or updates the router for WS command dispatch."""
        self.router = router

    def _build_hw_events(self) -> List[Dict[str, Any]]:
        """Constructs hardware telemetry events expected by Miniserver."""
        uptime = int(time.time() - self._start_time)
        client_id = f"{self.config.clean_mac}#1"
        return [
            {"client_id": client_id, "event_id": 2005, "value": 0},
            {"client_id": client_id, "event_id": 2101, "value": 67},
            {"client_id": client_id, "event_id": 2100, "value": 0},
            {"client_id": client_id, "event_id": 2102, "value": 0},
            {"client_id": client_id, "event_id": 2103, "value": 0},
            {"client_id": client_id, "event_id": 2105, "value": uptime},
            {"client_id": client_id, "event_id": 2106, "value": 56},
        ]

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
        # Support requested subprotocols (specifically 'miniserver' requested by Loxone Miniserver)
        requested_proto = request.headers.get("Sec-WebSocket-Protocol")
        protocols: tuple[str, ...] = (
            tuple(p.strip() for p in requested_proto.split(","))
            if requested_proto
            else ("miniserver",)
        )
        ws = web.WebSocketResponse(heartbeat=30.0, protocols=protocols)
        await ws.prepare(request)

        entry = (ws, server_name)
        self.connections.add(entry)
        logger.info(
            "Loxone WebSocket connected on %s from %s (total: %d)",
            server_name,
            request.remote,
            len(self.connections),
        )

        try:
            # 1. Send identification string
            greeting = self.config.get_identification(server_name)
            await ws.send_str(greeting)

            # 2. If msHttp, send hw_event hardware status immediately
            if server_name == "msHttp":
                hw_payload = json.dumps({"hw_event": self._build_hw_events()})
                await ws.send_str(hw_payload)

            # 3. Send current state of all zones on connect
            for state in self.get_all_states():
                try:
                    await ws.send_str(json.dumps({"audio_event": [state.to_dict()]}))
                except Exception:
                    break

            # 4. Listen for incoming messages and execute commands via router
            async for msg in ws:
                if msg.type == web.WSMsgType.TEXT:
                    command = msg.data.strip()
                    logger.debug("Received WS text from %s: %s", server_name, command)
                    if command and self.router:
                        try:
                            result = await self.router.dispatch(command)
                            response_text = serialize_result(result)
                            await ws.send_str(response_text)
                        except Exception as exc:
                            logger.warning("Error processing WS command '%s': %s", command, exc)
                            await ws.send_str("")
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
                hw_payload = json.dumps({"hw_event": self._build_hw_events()})
                for ws, srv in list(self.connections):
                    if srv == "msHttp":
                        try:
                            await ws.send_str(hw_payload)
                        except Exception:
                            self.connections.discard((ws, srv))
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.warning("Error in heartbeat loop: %s", exc)
