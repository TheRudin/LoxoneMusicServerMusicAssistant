"""Dual HTTP and WebSocket server hosting appHttp (7091) and msHttp (7095)."""

from __future__ import annotations

import logging
from typing import Optional

from aiohttp import web

from .responses import serialize_result
from .router import LoxoneRouter
from .websocket import LoxoneWebSocketNotifier
from .config import ServerConfig

logger = logging.getLogger("loxone.server")


class LoxoneServer:
    """Hosts Loxone HTTP and WebSocket endpoints on ports 7091 (app) and 7095 (miniserver)."""

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

    async def start(self) -> None:
        """Starts both HTTP/WebSocket servers."""
        # 1. Start WebSocket notifier heartbeat
        await self.notifier.start()

        # 2. Start appHttp on port 7091
        app_app = self._create_app("appHttp")
        self.app_runner = web.AppRunner(app_app)
        await self.app_runner.setup()
        app_site = web.TCPSite(self.app_runner, self.config.host, self.config.app_port)
        await app_site.start()
        logger.info("appHttp server listening on %s:%d", self.config.host, self.config.app_port)

        # 3. Start msHttp on port 7095
        ms_app = self._create_app("msHttp")
        self.ms_runner = web.AppRunner(ms_app)
        await self.ms_runner.setup()
        ms_site = web.TCPSite(self.ms_runner, self.config.host, self.config.ms_port)
        await ms_site.start()
        logger.info("msHttp server listening on %s:%d", self.config.host, self.config.ms_port)

    async def stop(self) -> None:
        """Stops both servers and cleans up connections."""
        await self.notifier.stop()

        if self.app_runner:
            await self.app_runner.cleanup()
            self.app_runner = None

        if self.ms_runner:
            await self.ms_runner.cleanup()
            self.ms_runner = None

        logger.info("Loxone servers stopped")
