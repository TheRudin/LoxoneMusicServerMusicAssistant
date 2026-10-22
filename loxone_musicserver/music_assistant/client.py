"""Async WebSocket RPC client for Music Assistant Server."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp

logger = logging.getLogger("music_assistant.client")

EventCallback = Callable[[Dict[str, Any]], None]


class MusicAssistantClient:
    """Minimal, robust WebSocket RPC client for Music Assistant 2.x."""

    def __init__(
        self,
        base_url: str,
        username: str = "admin",
        password: str = "",
        token: str = "",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.token = token

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._msg_id = 0
        self._pending: Dict[str, asyncio.Future[Any]] = {}
        self._event_handlers: List[EventCallback] = []
        self._running = False
        self._reader_task: Optional[asyncio.Task[None]] = None
        self.server_info: Dict[str, Any] = {}
        self.authenticated = False

    @property
    def ws_url(self) -> str:
        parsed = urlparse(self.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        netloc = parsed.netloc or f"{parsed.hostname}:{parsed.port or 8095}"
        return f"{scheme}://{netloc}/ws"

    def register_event_handler(self, handler: EventCallback) -> None:
        """Subscribes an event callback."""
        self._event_handlers.append(handler)

    async def connect(self) -> bool:
        """Connects to Music Assistant, performs authentication and starts reader."""
        if self._ws and not self._ws.closed:
            return True

        if not self._session:
            self._session = aiohttp.ClientSession()

        try:
            logger.info("Connecting to Music Assistant at %s ...", self.ws_url)
            self._ws = await self._session.ws_connect(self.ws_url, heartbeat=25.0)
            self._running = True
            self._reader_task = asyncio.create_task(self._reader_loop())

            # Wait for banner message
            await asyncio.sleep(0.1)

            # Authenticate if password or token is present
            if self.token:
                await self._authenticate_with_token(self.token)
            elif self.username and self.password:
                await self._authenticate_with_credentials(self.username, self.password)
            else:
                self.authenticated = True

            logger.info("Successfully connected to Music Assistant (Server: %s)", self.server_info.get("name", "MA"))
            return True
        except Exception as exc:
            logger.error("Failed to connect to Music Assistant: %s", exc)
            return False

    async def _authenticate_with_credentials(self, user: str, pwd: str) -> None:
        """Performs auth/login and validates the session."""
        login_res = await self.rpc(
            "auth/login",
            {
                "username": user,
                "password": pwd,
                "device_name": "Loxone Bridge",
            },
        )
        if isinstance(login_res, dict) and "access_token" in login_res:
            self.token = login_res["access_token"]
            await self._authenticate_with_token(self.token)
        else:
            raise RuntimeError(f"Login failed: {login_res}")

    async def _authenticate_with_token(self, token: str) -> None:
        """Sends auth command with bearer token."""
        auth_res = await self.rpc("auth", {"token": token})
        self.authenticated = True
        logger.debug("Music Assistant authentication successful: %s", auth_res)

    async def rpc(self, command: str, args: Optional[Dict[str, Any]] = None, timeout: float = 15.0) -> Any:
        """Executes an RPC command over WebSocket and returns the result."""
        if not self._ws or self._ws.closed:
            raise ConnectionError("WebSocket to Music Assistant is not open")

        self._msg_id += 1
        msg_id_str = str(self._msg_id)
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[msg_id_str] = future

        payload: Dict[str, Any] = {"command": command, "message_id": self._msg_id}
        if args is not None:
            payload["args"] = args

        await self._ws.send_json(payload)

        try:
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(msg_id_str, None)

    async def _reader_loop(self) -> None:
        """Reads incoming frames, resolves pending RPC futures, and dispatches events."""
        assert self._ws is not None
        try:
            async for msg in self._ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    try:
                        data = json.loads(msg.data)
                    except Exception:
                        continue

                    # Server initial banner
                    if "server_id" in data and "message_id" not in data:
                        self.server_info = data
                        continue

                    # Handle command response
                    msg_id = str(data.get("message_id", ""))
                    if msg_id and msg_id in self._pending:
                        fut = self._pending[msg_id]
                        if "error_code" in data:
                            err_msg = data.get("details") or data.get("error_message") or f"RPC error {data.get('error_code')}"
                            fut.set_exception(RuntimeError(err_msg))
                        else:
                            fut.set_result(data.get("result", data))

                    # Handle event broadcast
                    if "event" in data:
                        for handler in self._event_handlers:
                            try:
                                handler(data)
                            except Exception as exc:
                                logger.warning("Error in MA event callback: %s", exc)

                elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                    break
        except Exception as exc:
            logger.warning("MA WebSocket reader encountered exception: %s", exc)
        finally:
            logger.info("MA WebSocket connection closed")
            self._running = False
            # Cancel all pending futures
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("Connection closed"))
            self._pending.clear()

    # --- High Level Music Assistant Commands ---

    async def get_players(self) -> List[Dict[str, Any]]:
        """Retrieves all players from Music Assistant."""
        res = await self.rpc("players/all")
        return res if isinstance(res, list) else []

    async def get_player_queues(self) -> List[Dict[str, Any]]:
        """Retrieves all player queues."""
        res = await self.rpc("player_queues/all")
        return res if isinstance(res, list) else []

    async def player_cmd(self, player_id: str, cmd: str, args: Optional[Dict[str, Any]] = None) -> Any:
        """Executes a player command (e.g. play, pause, stop, volume_set)."""
        full_args: Dict[str, Any] = {"player_id": player_id}
        if args:
            full_args.update(args)
        return await self.rpc(f"players/cmd/{cmd}", full_args)

    async def queue_cmd(self, queue_id: str, cmd: str, args: Optional[Dict[str, Any]] = None) -> Any:
        """Executes a queue command."""
        full_args: Dict[str, Any] = {"queue_id": queue_id}
        if args:
            full_args.update(args)
        return await self.rpc(f"player_queues/cmd/{cmd}", full_args)

    async def play_media(self, queue_id: str, media: str, option: str = "play") -> Any:
        """Plays media item in player queue."""
        return await self.rpc(
            "player_queues/play_media",
            {
                "queue_id": queue_id,
                "media": media,
                "option": option,
            },
        )

    async def get_queue_items(self, queue_id: str, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Gets items in queue."""
        res = await self.rpc("player_queues/items", {"queue_id": queue_id, "limit": limit, "offset": offset})
        return res if isinstance(res, list) else []

    async def get_radios(self) -> List[Dict[str, Any]]:
        """Gets radio stations from library."""
        res = await self.rpc("music/radios/library_items")
        return res if isinstance(res, list) else []

    async def get_playlists(self, limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
        """Gets playlists from library."""
        res = await self.rpc("music/playlists/library_items", {"limit": limit, "offset": offset})
        return res if isinstance(res, list) else []

    async def close(self) -> None:
        """Closes connection."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._session:
            await self._session.close()
            self._session = None
