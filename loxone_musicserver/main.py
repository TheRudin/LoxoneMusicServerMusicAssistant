"""CLI Entrypoint and standalone daemon for Loxone Music Server emulator."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import re
import signal
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import ServerConfig
from .loxone.crypto import LoxoneCrypto
from .loxone.discovery import LoxoneDiscoveryService
from .loxone.handlers.alert_handlers import AlertHandlers
from .loxone.handlers.config_handlers import ConfigHandlers
from .loxone.handlers.favorite_handlers import FavoriteHandlers
from .loxone.handlers.queue_handlers import QueueHandlers
from .loxone.handlers.secure_handlers import SecureHandlers
from .loxone.handlers.zone_handlers import ZoneHandlers
from .loxone.router import LoxoneRouter
from .loxone.server import LoxoneServer
from .loxone.websocket import LoxoneWebSocketNotifier
from .music_assistant.client import MusicAssistantClient
from .music_assistant.sync import MusicAssistantZoneSync

logger = logging.getLogger("loxone_musicserver")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Loxone Music Server / Audioserver Emulation for Music Assistant"
    )
    parser.add_argument(
        "--ma-url",
        default=os.environ.get("MA_URL", "http://localhost:8095"),
        help="Music Assistant server URL (default: http://localhost:8095 or MA_URL)",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("MA_USERNAME", "admin"),
        help="Music Assistant username (default: admin or MA_USERNAME)",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("MA_PASSWORD", ""),
        help="Music Assistant password (or MA_PASSWORD)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("MA_TOKEN", ""),
        help="Music Assistant access token (optional if user/pass provided)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("LOXONE_HOST", "0.0.0.0"),
        help="Binding host interface (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--app-port",
        type=int,
        default=int(os.environ.get("LOXONE_APP_PORT", "7091")),
        help="Loxone App HTTP port (default: 7091)",
    )
    parser.add_argument(
        "--ms-port",
        type=int,
        default=int(os.environ.get("LOXONE_MS_PORT", "7095")),
        help="Loxone Miniserver HTTP port (default: 7095)",
    )
    parser.add_argument(
        "--discovery-port",
        type=int,
        default=int(os.environ.get("LOXONE_DISCOVERY_PORT", "7070")),
        help="Loxone UDP discovery port (default: 7070)",
    )
    parser.add_argument(
        "--mac",
        default=os.environ.get("LOXONE_MAC", ""),
        help="Audioserver MAC address (optional, auto-generated if omitted)",
    )
    parser.add_argument(
        "--data-dir",
        default=os.environ.get("LOXONE_DATA_DIR", "./data"),
        help="Directory to persist configurations (default: ./data)",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("LOG_LEVEL", "INFO"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level (default: INFO)",
    )
    return parser.parse_args()


async def run_daemon(args: argparse.Namespace) -> None:
    # 1. Setup Config
    config = ServerConfig(
        host=args.host,
        app_port=args.app_port,
        ms_port=args.ms_port,
        discovery_port=args.discovery_port,
        ma_url=args.ma_url,
        ma_username=args.username,
        ma_password=args.password,
        ma_token=args.token,
        data_dir=Path(args.data_dir),
    )
    if args.mac:
        config.mac_address = args.mac

    config.data_dir.mkdir(parents=True, exist_ok=True)

    crypto = LoxoneCrypto()
    router = LoxoneRouter()

    # 2. Client & Synchronizer
    ma_client = MusicAssistantClient(
        base_url=config.ma_url,
        username=config.ma_username,
        password=config.ma_password,
        token=config.ma_token,
    )

    notifier = LoxoneWebSocketNotifier(config, get_all_states=lambda: [])
    sync = MusicAssistantZoneSync(config, ma_client, notifier)
    notifier.get_all_states = sync.get_all_states

    # 3. Setup Handlers
    secure = SecureHandlers()
    cfg = ConfigHandlers(config, crypto, on_config_updated=sync.apply_loxone_config)
    zone = ZoneHandlers(sync.get_zone_state, sync.handle_player_command)
    queue = QueueHandlers(sync.get_queue_data, sync.handle_queue_control)
    favs = FavoriteHandlers(
        sync.get_favorites_data,
        sync.play_favorite,
        sync.cycle_favorite,
        ma_client.get_radios,
        ma_client.get_playlists,
        sync.handle_play_media,
    )
    alerts = AlertHandlers(sync.play_tts, sync.play_alert)

    # 4. Register Routes
    router.register_prefix("secure/hello", secure.hello)
    router.register_prefix("secure/info/pairing", secure.info_pairing)
    router.register_prefix("secure/authenticate", secure.authenticate)
    router.register_prefix("secure/init", secure.init)

    router.register_prefix("audio/cfg/getkey/full", cfg.get_key_full)
    router.register_prefix("audio/cfg/getkey", cfg.get_key)
    router.register_prefix("audio/cfg/getconfig", cfg.get_config)
    router.register_prefix("audio/cfg/setconfigtimestamp", cfg.set_config_timestamp)
    router.register_prefix("audio/cfg/setconfig", cfg.set_config)
    router.register_prefix("audio/cfg/ready", cfg.ready)
    router.register_prefix("audio/cfg/miniservertime", cfg.miniserver_time)
    router.register_prefix("audio/cfg/identify", cfg.identify)
    router.register_prefix("audio/cfg/diagnosis", cfg.diagnosis)
    router.register_prefix("audio/cfg/restart", cfg.restart)

    router.register_prefix("audio/cfg/getservices", favs.get_services)
    router.register_prefix("audio/cfg/getavailableservices", favs.get_available_services)
    router.register_prefix("audio/cfg/getradios", favs.get_radios)
    router.register_prefix("audio/cfg/getplaylists2", favs.get_playlists)
    router.register_prefix("audio/cfg/getroomfavs", favs.get_room_favs)
    router.register_prefix("audio/cfg/roomfavs", favs.room_favs_action)

    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/status/?$"), zone.get_status)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/getqueue"), queue.get_queue)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/queue/clear"), queue.queue_clear)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/queue/play/"), queue.queue_play)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/queueandplay/"), queue.queue_and_play)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/queueadd/"), queue.queue_add)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/serviceplay/"), favs.service_play)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/playurl/"), favs.play_url)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/roomfav/play/"), favs.room_fav_play)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/roomfav/plus/?$"), favs.room_fav_plus)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/tts/"), alerts.zone_tts)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/(?:bell|alarm|firealarm|clock|buzzer)"), alerts.zone_alert)
    router.register_regex(re.compile(r"^audio/grouped/"), alerts.grouped_alert)
    router.register_regex(
        re.compile(r"^audio/(?:cfg/)?\d+/(?:on|off|play|pause|resume|position|volume|queueplus|queueminus|repeat|shuffle)"),
        zone.dynamic_command,
    )

    # 5. Start Servers
    server = LoxoneServer(config, router, notifier)
    discovery = LoxoneDiscoveryService(config)

    logger.info("Connecting to Music Assistant at %s ...", config.ma_url)
    connected = await ma_client.connect()
    if connected:
        await sync.initialize()
    else:
        logger.warning("Could not immediately connect to Music Assistant; will retry in background.")

    await discovery.start()
    await server.start()

    logger.info("================================================================")
    logger.info(" Loxone Music Server Emulation ready!")
    logger.info(" Audioserver Name: %s", config.server_name)
    logger.info(" Audioserver MAC:  %s", config.formatted_mac)
    logger.info(" UDP Discovery:    Port %d", config.discovery_port)
    logger.info(" App HTTP/WS:      Port %d", config.app_port)
    logger.info(" Miniserver HTTP:  Port %d", config.ms_port)
    logger.info(" Connected MA:     %s", config.ma_url)
    logger.info("================================================================")

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    try:
        await stop_event.wait()
    finally:
        logger.info("Shutting down...")
        await server.stop()
        await discovery.stop()
        await ma_client.close()
        logger.info("Shutdown complete.")


def main() -> None:
    args = parse_arguments()
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    try:
        asyncio.run(run_daemon(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
