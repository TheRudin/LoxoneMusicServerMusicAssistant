"""UDP 7070 discovery responder and mDNS advertiser for Loxone."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from typing import Optional

from .config import ServerConfig

logger = logging.getLogger("loxone.discovery")


class LoxoneUdpDiscoveryProtocol(asyncio.DatagramProtocol):
    """UDP protocol handler for port 7070 discovery probes."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.transport: Optional[asyncio.DatagramTransport] = None

    @property
    def response_payload(self) -> bytes:
        return json.dumps(
            {
                "t": "Audioserver",
                "tv": "3",
                "n": self.config.server_name,
                "s": self.config.formatted_mac,
                "v": self.config.firmware_version_number,
                "p": str(self.config.app_port),
                "hn": "audioserver",
                "di": "1",
            }
        ).encode("utf-8")

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]
        logger.info("UDP discovery responder listening on port %d", self.config.discovery_port)

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        logger.debug("Received UDP discovery probe from %s:%d (%d bytes)", addr[0], addr[1], len(data))
        if self.transport:
            try:
                self.transport.sendto(self.response_payload, addr)
                logger.debug("Sent UDP discovery response to %s:%d", addr[0], addr[1])
            except Exception as exc:
                logger.warning("Failed to send UDP discovery response to %s:%d: %s", addr[0], addr[1], exc)


class LoxoneDiscoveryService:
    """Coordinates UDP discovery and mDNS broadcasting."""

    def __init__(self, config: ServerConfig) -> None:
        self.config = config
        self.udp_transport: Optional[asyncio.DatagramTransport] = None
        self.zeroconf = None
        self.service_info = None

    async def start(self) -> None:
        """Starts both UDP responder and mDNS advertisement."""
        loop = asyncio.get_running_loop()

        # 1. Start UDP 7070 listener with SO_REUSEADDR and SO_REUSEPORT (if available)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            if hasattr(socket, "SO_REUSEPORT"):
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                except Exception:
                    pass
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((self.config.host, self.config.discovery_port))
            sock.setblocking(False)

            transport, _ = await loop.create_datagram_endpoint(
                lambda: LoxoneUdpDiscoveryProtocol(self.config),
                sock=sock,
            )
            self.udp_transport = transport
        except Exception as exc:
            logger.warning("Could not bind UDP discovery on port %d: %s", self.config.discovery_port, exc)

        # 2. Start mDNS advertisement
        await self._start_mdns()

    async def _start_mdns(self) -> None:
        try:
            from zeroconf import IPVersion, ServiceInfo
            from zeroconf.asyncio import AsyncZeroconf

            desc = {
                "version": self.config.mdns_version,
                "txtvers": "2",
                "devicetype": "Audioserver",
                "deviceinstance": "2",
                "mac": self.config.formatted_mac,
            }

            self.zeroconf = AsyncZeroconf(ip_version=IPVersion.V4Only)
            self.service_info = ServiceInfo(
                "_http._tcp.local.",
                f"{self.config.server_name}._http._tcp.local.",
                addresses=[socket.inet_aton(self._get_local_ip())],
                port=self.config.app_port,
                properties=desc,
                server="audioserver.local.",
            )
            await self.zeroconf.async_register_service(self.service_info)
            logger.info("mDNS service registered for Audioserver on port %d", self.config.app_port)
        except Exception as exc:
            logger.warning("mDNS registration skipped or failed: %s", exc)

    def _get_local_ip(self) -> str:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "127.0.0.1"

    async def stop(self) -> None:
        """Stops UDP listener and unregisters mDNS."""
        if self.udp_transport:
            self.udp_transport.close()
            self.udp_transport = None

        if self.zeroconf and self.service_info:
            try:
                await self.zeroconf.async_unregister_service(self.service_info)
                await self.zeroconf.async_close()
            except Exception as exc:
                logger.warning("Error stopping mDNS: %s", exc)
            self.zeroconf = None
            self.service_info = None
