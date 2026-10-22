"""Comprehensive End-to-End Endpoint Verification Suite for Loxone Music Server."""

import asyncio
import json
import socket
import sys
import aiohttp
import re

from loxone_musicserver.config import ServerConfig
from loxone_musicserver.music_assistant.client import MusicAssistantClient
from loxone_musicserver.music_assistant.sync import MusicAssistantZoneSync
from loxone_musicserver.loxone.crypto import LoxoneCrypto
from loxone_musicserver.loxone.router import LoxoneRouter
from loxone_musicserver.loxone.server import LoxoneServer
from loxone_musicserver.loxone.discovery import LoxoneDiscoveryService
from loxone_musicserver.loxone.websocket import LoxoneWebSocketNotifier
from loxone_musicserver.loxone.handlers.alert_handlers import AlertHandlers
from loxone_musicserver.loxone.handlers.config_handlers import ConfigHandlers
from loxone_musicserver.loxone.handlers.favorite_handlers import FavoriteHandlers
from loxone_musicserver.loxone.handlers.queue_handlers import QueueHandlers
from loxone_musicserver.loxone.handlers.secure_handlers import SecureHandlers
from loxone_musicserver.loxone.handlers.zone_handlers import ZoneHandlers

results = []

def record(name: str, passed: bool, details: str = ""):
    status = "PASS" if passed else "FAIL"
    results.append((name, status, details))
    print(f"[{status}] {name}: {details}")

async def run_verification():
    print("============================================================")
    print("STARTING LOXONE MUSIC SERVER ENDPOINT VERIFICATION SUITE")
    print("============================================================")
    
    cfg = ServerConfig(
        ma_url="http://192.168.1.10:8095",
        ma_username="admin",
        ma_password="your_password",
        app_port=7091,
        ms_port=7095,
        discovery_port=7070,
    )
    
    # 1. Connect to live Music Assistant
    ma_client = MusicAssistantClient(cfg.ma_url, cfg.ma_username, cfg.ma_password)
    connected = await ma_client.connect()
    record("Music Assistant Live Connection", connected, f"Server: {ma_client.server_info.get('name')}")
    if not connected:
        print("Aborting: Cannot connect to Music Assistant")
        return

    # 2. Setup Sync and handlers
    crypto = LoxoneCrypto()
    router = LoxoneRouter()
    notifier = LoxoneWebSocketNotifier(cfg, get_all_states=lambda: sync.get_all_states())
    sync = MusicAssistantZoneSync(cfg, ma_client, notifier)

    server = LoxoneServer(cfg, router, notifier)
    discovery = LoxoneDiscoveryService(cfg)

    secure = SecureHandlers()
    cfg_handlers = ConfigHandlers(cfg, crypto, sync.apply_loxone_config)
    zone_handlers = ZoneHandlers(sync.get_zone_state, sync.handle_player_command)
    queue_handlers = QueueHandlers(sync.get_queue_data, sync.handle_queue_control)
    fav_handlers = FavoriteHandlers(
        sync.get_favorites_data,
        sync.play_favorite,
        sync.cycle_favorite,
        ma_client.get_radios,
        ma_client.get_playlists,
        sync.handle_play_media,
    )
    alert_handlers = AlertHandlers(sync.play_tts, sync.play_alert)

    # Register routes
    router.register_prefix("secure/hello", secure.hello)
    router.register_prefix("secure/info/pairing", secure.info_pairing)
    router.register_prefix("secure/authenticate", secure.authenticate)
    router.register_prefix("secure/init", secure.init)

    router.register_prefix("audio/cfg/getkey/full", cfg_handlers.get_key_full)
    router.register_prefix("audio/cfg/getkey", cfg_handlers.get_key)
    router.register_prefix("audio/cfg/getconfig", cfg_handlers.get_config)
    router.register_prefix("audio/cfg/setconfigtimestamp", cfg_handlers.set_config_timestamp)
    router.register_prefix("audio/cfg/setconfig", cfg_handlers.set_config)
    router.register_prefix("audio/cfg/ready", cfg_handlers.ready)
    router.register_prefix("audio/cfg/miniservertime", cfg_handlers.miniserver_time)
    router.register_prefix("audio/cfg/identify", cfg_handlers.identify)
    router.register_prefix("audio/cfg/diagnosis", cfg_handlers.diagnosis)
    router.register_prefix("audio/cfg/restart", cfg_handlers.restart)

    router.register_prefix("audio/cfg/getservices", fav_handlers.get_services)
    router.register_prefix("audio/cfg/getavailableservices", fav_handlers.get_available_services)
    router.register_prefix("audio/cfg/getradios", fav_handlers.get_radios)
    router.register_prefix("audio/cfg/getplaylists2", fav_handlers.get_playlists)
    router.register_prefix("audio/cfg/getroomfavs", fav_handlers.get_room_favs)
    router.register_prefix("audio/cfg/roomfavs", fav_handlers.room_favs_action)

    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/status/?$"), zone_handlers.get_status)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/getqueue"), queue_handlers.get_queue)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/queue/clear"), queue_handlers.queue_clear)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/queue/play/"), queue_handlers.queue_play)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/queueandplay/"), queue_handlers.queue_and_play)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/queueadd/"), queue_handlers.queue_add)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/serviceplay/"), fav_handlers.service_play)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/playurl/"), fav_handlers.play_url)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/roomfav/play/"), fav_handlers.room_fav_play)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/roomfav/plus/?$"), fav_handlers.room_fav_plus)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/tts/"), alert_handlers.zone_tts)
    router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/(?:bell|alarm|firealarm|clock|buzzer)"), alert_handlers.zone_alert)
    router.register_regex(re.compile(r"^audio/grouped/"), alert_handlers.grouped_alert)
    router.register_regex(
        re.compile(r"^audio/(?:cfg/)?\d+/(?:on|off|play|pause|resume|position|volume|queueplus|queueminus|repeat|shuffle)"),
        zone_handlers.dynamic_command,
    )

    # 3. Start services
    await sync.initialize()
    record("Zone Mapping Discovery", len(sync.zone_to_player) > 0, f"{len(sync.zone_to_player)} zones discovered")
    for zid, pid in sync.zone_to_player.items():
        state = sync.get_zone_state(zid)
        print(f"   - Zone {zid}: {state.name if state else pid} ({pid})")

    await discovery.start()
    await server.start()
    record("Loxone Servers Started", True, "Discovery 7070, App 7091, MS 7095")

    # 4. Run HTTP endpoint tests
    async with aiohttp.ClientSession() as session:
        http_endpoints = [
            ("audio/cfg/ready", "GET /audio/cfg/ready"),
            ("audio/cfg/getkey/full", "GET /audio/cfg/getkey/full (RSA SPKI)"),
            ("audio/cfg/getkey", "GET /audio/cfg/getkey (Modulus/Exp)"),
            ("audio/cfg/getconfig", "GET /audio/cfg/getconfig"),
            ("audio/cfg/getservices", "GET /audio/cfg/getservices"),
            ("audio/cfg/getavailableservices", "GET /audio/cfg/getavailableservices"),
            ("audio/cfg/getradios", "GET /audio/cfg/getradios"),
            ("audio/cfg/getplaylists2", "GET /audio/cfg/getplaylists2"),
            ("audio/1/status", "GET /audio/1/status (Zone 1 Status)"),
            ("audio/2/status", "GET /audio/2/status (Zone 2 Status)"),
            ("audio/3/status", "GET /audio/3/status (Zone 3 Status)"),
            ("audio/4/status", "GET /audio/4/status (Zone 4 Status)"),
            ("audio/1/volume/33", "GET /audio/1/volume/33 (Set volume 33)"),
            ("audio/1/pause", "GET /audio/1/pause"),
            ("audio/1/play", "GET /audio/1/play"),
            ("audio/1/tts/Hallo%20Welt", "GET /audio/1/tts/Hallo Welt"),
            ("audio/1/bell", "GET /audio/1/bell"),
            ("audio/1/getqueue", "GET /audio/1/getqueue"),
        ]

        for path, label in http_endpoints:
            url = f"http://127.0.0.1:7091/{path}"
            try:
                async with session.get(url, timeout=4.0) as resp:
                    text = await resp.text()
                    is_ok = resp.status == 200 and len(text) > 0
                    record(label, is_ok, f"Status {resp.status}, Bytes: {len(text)}")
            except Exception as e:
                record(label, False, str(e))

        # 5. Test WebSocket port 7091 (App)
        try:
            async with session.ws_connect("ws://127.0.0.1:7091/", timeout=4.0) as ws:
                msg = await ws.receive_str(timeout=4.0)
                record("WebSocket 7091 Greeting (App)", "LWSS V 17.1.05.05" in msg, f"Greeting: {msg.strip()}")
        except Exception as e:
            record("WebSocket 7091 Greeting (App)", False, str(e))

        # 6. Test WebSocket port 7095 (Miniserver)
        try:
            async with session.ws_connect("ws://127.0.0.1:7095/", timeout=4.0) as ws:
                msg = await ws.receive_str(timeout=4.0)
                record("WebSocket 7095 Greeting (MS)", "LWSS V 17.1.05.05" in msg, f"Greeting: {msg.strip()}")
                
                # Check for heartbeat within 12 seconds
                hb_msg = await ws.receive_str(timeout=12.0)
                record("WebSocket 7095 Heartbeat", "server_heartbeat" in hb_msg, f"Heartbeat: {hb_msg.strip()}")
        except Exception as e:
            record("WebSocket 7095 Communication", False, str(e))

    # 7. Test UDP Discovery 7070
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3.0)
        sock.sendto(b"Loxone AudioServer Discover", ("127.0.0.1", 7070))
        loop = asyncio.get_running_loop()
        data, addr = await loop.run_in_executor(None, sock.recvfrom, 2048)
        response_text = data.decode("utf-8", errors="ignore")
        is_disc_ok = "Audioserver" in response_text or cfg.server_name in response_text
        record("UDP 7070 Discovery Probe", is_disc_ok, f"Reply: {response_text.strip()}")
        sock.close()
    except Exception as e:
        record("UDP 7070 Discovery Probe", False, str(e))

    # 8. Teardown
    await server.stop()
    await discovery.stop()
    await ma_client.close()

    print("")
    print("============================================================")
    print("VERIFICATION SUMMARY")
    print("============================================================")
    total = len(results)
    passed = sum(1 for _, s, _ in results if s == "PASS")
    print(f"Total Tests: {total} | Passed: {passed} | Failed: {total - passed}")
    if passed == total:
        print("ALL ENDPOINTS OPERATIONAL AND VERIFIED!")
    else:
        print("SOME CHECKS FAILED!")

if __name__ == "__main__":
    asyncio.run(run_verification())
