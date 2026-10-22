"""Dual HTTP and WebSocket server hosting appHttp (7091) and msHttp (7095) with Dual-Protocol TLS."""

from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from aiohttp import web

from ..config import ServerConfig
from .responses import serialize_result
from .router import LoxoneRouter
from .tls import get_or_create_tls_context
from .websocket import LoxoneWebSocketNotifier

logger = logging.getLogger("loxone.server")


class LoxoneServer:
    """Hosts Loxone HTTP and WebSocket endpoints on ports 7091 (app) and 7095 (miniserver).

    Supports dual-protocol dispatching: transparently multiplexes plain HTTP/WS and
    encrypted HTTPS/WSS (TLS) traffic on the same port, as required by Loxone Miniserver Gen 2+.
    """

    def __init__(
        self,
        config: ServerConfig,
        router: LoxoneRouter,
        notifier: LoxoneWebSocketNotifier,
    ) -> None:
        self.config = config
        self.router = router
        self.notifier = notifier
        self.app_runner: Optional[web.AppRunner] = None
        self.ms_runner: Optional[web.AppRunner] = None
        self.listeners: List[asyncio.AbstractServer] = []
        self._forward_tasks: set[asyncio.Task] = set()

    def _create_app(self, server_name: str) -> web.Application:
        app = web.Application()

        async def handler(request: web.Request) -> web.StreamResponse:
            # Check for WebSocket upgrade
            if request.headers.get("Upgrade", "").lower() == "websocket":
                return await self.notifier.handle_connection(request, server_name)

            # Handle OPTIONS preflight
            if request.method == "OPTIONS":
                return web.Response(
                    headers={
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                        "Access-Control-Allow-Headers": "Content-Type, Authorization",
                    }
                )

            # Read HTTP body if present
            payload = await request.read() if request.can_read_body else None
            path = request.match_info.get("path", "")
            if not path:
                path = request.path.lstrip("/")

            logger.debug("[%s] HTTP %s /%s", server_name, request.method, path)
            if not path.startswith("audio/cfg/diagnosis"):
                self.notifier.record_command(server_name, "http", f"{request.method} /{path}")

            result = await self.router.dispatch(path, payload)
            response_text = serialize_result(result)

            return web.Response(
                text=response_text,
                content_type="application/json",
                headers={
                    "Access-Control-Allow-Origin": "*",
                },
            )

        app.router.add_route("*", "/{path:.*}", handler)
        return app

    async def _start_dual_protocol_listener(
        self,
        runner: web.AppRunner,
        server_name: str,
        public_port: int,
        tls_ctx: Optional[object],
    ) -> None:
        """Starts backend plain + SSL sites and a public dual-protocol dispatcher."""
        # 1. Plain HTTP/WS backend site on loopback with OS-assigned port
        plain_site = web.TCPSite(runner, "127.0.0.1", 0)
        await plain_site.start()
        plain_port = plain_site._server.sockets[0].getsockname()[1]

        # 2. SSL/TLS backend site (if TLS context available)
        ssl_port = None
        if tls_ctx:
            ssl_site = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=tls_ctx)
            await ssl_site.start()
            ssl_port = ssl_site._server.sockets[0].getsockname()[1]

        # 3. If no TLS, bind plain directly
        if not ssl_port:
            fallback_site = web.TCPSite(runner, self.config.host, public_port)
            await fallback_site.start()
            logger.info("%s server listening (plain only) on %s:%d", server_name, self.config.host, public_port)
            return

        async def _forward(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    writer.write(data)
                    await writer.drain()
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                except Exception:
                    pass

        async def dispatch(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
            target_writer = None
            try:
                peek = await client_reader.read(1)
                if not peek:
                    client_writer.close()
                    return

                # 0x16 is TLS Handshake (ClientHello)
                is_tls = peek[0] == 0x16
                target_port = ssl_port if is_tls else plain_port
                target_reader, target_writer = await asyncio.open_connection("127.0.0.1", target_port)
                target_writer.write(peek)
                await target_writer.drain()

                t1 = asyncio.create_task(_forward(client_reader, target_writer))
                t2 = asyncio.create_task(_forward(target_reader, client_writer))
                self._forward_tasks.add(t1)
                self._forward_tasks.add(t2)
                t1.add_done_callback(self._forward_tasks.discard)
                t2.add_done_callback(self._forward_tasks.discard)
            except Exception as exc:
                logger.debug("[%s] Dispatcher error: %s", server_name, exc)
                try:
                    client_writer.close()
                except Exception:
                    pass
                if target_writer:
                    try:
                        target_writer.close()
                    except Exception:
                        pass

        listener = await asyncio.start_server(dispatch, self.config.host, public_port)
        self.listeners.append(listener)
        logger.info(
            "%s dual-protocol server (HTTP/WS + HTTPS/WSS) listening on %s:%d",
            server_name,
            self.config.host,
            public_port,
        )

    async def start(self) -> None:
        """Starts both HTTP/WebSocket servers with Dual-Protocol TLS support."""
        # 1. Start WebSocket notifier heartbeat
        await self.notifier.start()

        # 2. Load or generate self-signed TLS certificate
        tls_ctx = get_or_create_tls_context(self.config.data_dir)

        # 3. Start appHttp on port 7091
        app_app = self._create_app("appHttp")
        self.app_runner = web.AppRunner(app_app)
        await self.app_runner.setup()
        await self._start_dual_protocol_listener(self.app_runner, "appHttp", self.config.app_port, tls_ctx)

        # 4. Start msHttp on port 7095
        ms_app = self._create_app("msHttp")
        self.ms_runner = web.AppRunner(ms_app)
        await self.ms_runner.setup()
        await self._start_dual_protocol_listener(self.ms_runner, "msHttp", self.config.ms_port, tls_ctx)

    async def stop(self) -> None:
        """Stops all servers, listeners, and cleans up connections."""
        await self.notifier.stop()

        for listener in self.listeners:
            try:
                listener.close()
                await listener.wait_closed()
            except Exception:
                pass
        self.listeners.clear()

        for task in list(self._forward_tasks):
            task.cancel()
        self._forward_tasks.clear()

        if self.app_runner:
            await self.app_runner.cleanup()
            self.app_runner = None

        if self.ms_runner:
            await self.ms_runner.cleanup()
            self.ms_runner = None

        logger.info("Loxone servers stopped")
