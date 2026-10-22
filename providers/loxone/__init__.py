"""Music Assistant Native Plugin Provider for Loxone AudioServer Emulation."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import re
import time
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .config import ServerConfig
from .crypto import LoxoneCrypto
from .discovery import LoxoneDiscoveryService
from .handlers.alert_handlers import AlertHandlers
from .handlers.config_handlers import ConfigHandlers
from .handlers.favorite_handlers import DEFAULT_RADIO_STATIONS, FavoriteHandlers
from .handlers.queue_handlers import QueueHandlers
from .handlers.secure_handlers import SecureHandlers
from .handlers.zone_handlers import ZoneHandlers
from .router import LoxoneRouter
from .server import LoxoneServer
from .websocket import LoxoneWebSocketNotifier, LoxoneZoneState

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ConfigEntry, ProviderConfig
    from music_assistant_models.event import MassEvent
    from music_assistant_models.provider import ProviderManifest
    from music_assistant.mass import MusicAssistant

try:
    from music_assistant.models.plugin import PluginProvider
    from music_assistant_models.config_entries import ConfigEntry, ConfigValueOption
    from music_assistant_models.enums import ConfigEntryType
except ImportError:
    # Standalone / test fallback when running outside Music Assistant
    class PluginProvider:  # type: ignore[no-redef]
        def __init__(self, mass: Any, manifest: Any, config: Any) -> None:
            self.mass = mass
            self.manifest = manifest
            self.config = config

        def get_config_value(self, key: str, default: Any = None) -> Any:
            if hasattr(self.config, 'get_value'):
                return self.config.get_value(key)
            return default

    class ConfigEntry:  # type: ignore[no-redef]
        def __init__(self, **kwargs: Any) -> None:
            self.__dict__.update(kwargs)

    class ConfigValueOption:  # type: ignore[no-redef]
        def __init__(self, title: str = "", value: Any = None, **kwargs: Any) -> None:
            self.title = title
            self.value = value
            self.__dict__.update(kwargs)

    class ConfigEntryType:  # type: ignore[no-redef]
        STRING = 'string'
        INTEGER = 'integer'
        BOOLEAN = 'boolean'

LOGGER = logging.getLogger('music_assistant.providers.loxone')

GITHUB_ASSETS_BASE = "https://raw.githubusercontent.com/TheRudin/LoxoneMusicServerMusicAssistant/main/assets"

CONF_SERVER_NAME = 'server_name'
CONF_LISTEN_IP = 'listen_ip'
CONF_APP_PORT = 'app_http_port'
CONF_MS_PORT = 'ms_http_port'
CONF_UDP_PORT = 'udp_discovery_port'
CONF_MAC_ADDRESS = 'mac_address'
CONF_MINISERVER_SERIAL = 'miniserver_serial'
CONF_MINISERVER_IP = 'miniserver_ip'

CONF_ZONE_1_PLAYER = 'zone_1_player'
CONF_ZONE_2_PLAYER = 'zone_2_player'
CONF_ZONE_3_PLAYER = 'zone_3_player'
CONF_ZONE_4_PLAYER = 'zone_4_player'
CONF_ZONE_5_PLAYER = 'zone_5_player'
CONF_ZONE_6_PLAYER = 'zone_6_player'
CONF_ZONE_7_PLAYER = 'zone_7_player'
CONF_ZONE_8_PLAYER = 'zone_8_player'


async def setup(
    mass: Any,
    manifest: Any,
    config: Any,
) -> Any:
    """Initialize provider instance with given configuration."""
    return LoxonePlugin(mass, manifest, config)


class LoxonePlugin(PluginProvider):
    """Loxone AudioServer plugin provider for Music Assistant."""

    def __init__(
        self,
        mass: Any,
        manifest: Any,
        config: Any,
    ) -> None:
        super().__init__(mass, manifest, config)
        self.server_config = ServerConfig()
        self.crypto = LoxoneCrypto()
        self.router = LoxoneRouter()

        self.zone_to_player: Dict[int, str] = {}
        self.player_to_zone: Dict[str, int] = {}
        self.zone_states: Dict[int, LoxoneZoneState] = {}
        self._radio_catalog: Dict[str, Dict[str, Any]] = {}

        self.notifier = LoxoneWebSocketNotifier(
            self.server_config,
            get_all_states=lambda: list(self.zone_states.values()),
            router=self.router,
        )
        self.discovery = LoxoneDiscoveryService(self.server_config)
        self.server = LoxoneServer(self.server_config, self.router, self.notifier)
        self._unsub: Optional[Any] = None

    async def get_config_entries(self) -> tuple[Any, ...]:
        """Return Config entries to configure this provider in Music Assistant UI."""
        # Collect players from Music Assistant for dynamic zone mapping dropdowns
        player_options: List[ConfigValueOption] = [
            ConfigValueOption(title="Automatisch (Nächster freier Player)", value="auto"),
            ConfigValueOption(title="Deaktiviert (Kein Player)", value="disabled"),
        ]
        players = []
        if hasattr(self.mass, "players"):
            if hasattr(self.mass.players, "_players") and isinstance(self.mass.players._players, dict):
                players = list(self.mass.players._players.values())
            elif hasattr(self.mass.players, "all") and isinstance(self.mass.players.all, (list, tuple)):
                players = list(self.mass.players.all)
            else:
                try:
                    players = list(self.mass.players)
                except Exception:
                    players = []

        for p in sorted(players, key=lambda x: (getattr(x, "display_name", None) or getattr(x, "name", "")).lower()):
            pid = getattr(p, "player_id", None)
            if not pid:
                continue
            pname = getattr(p, "display_name", None) or getattr(p, "name", pid)
            player_options.append(ConfigValueOption(title=f"{pname} ({pid})", value=str(pid)))

        entries = [
            ConfigEntry(
                key=CONF_SERVER_NAME,
                type=ConfigEntryType.STRING,
                default_value=self.server_config.server_name,
                label='Server Name',
                description='Name of the Loxone AudioServer emulated in Loxone Config and App',
                required=False,
            ),
            ConfigEntry(
                key=CONF_LISTEN_IP,
                type=ConfigEntryType.STRING,
                default_value=self.server_config.host,
                label='Bind IP Address',
                description='IP to bind listeners (0.0.0.0 for all interfaces)',
                required=False,
            ),
            ConfigEntry(
                key=CONF_APP_PORT,
                type=ConfigEntryType.INTEGER,
                default_value=self.server_config.app_port,
                label='Loxone App TCP Port',
                description='Port for Loxone App HTTP and WebSocket (7091)',
                required=False,
            ),
            ConfigEntry(
                key=CONF_MS_PORT,
                type=ConfigEntryType.INTEGER,
                default_value=self.server_config.ms_port,
                label='Loxone Miniserver TCP Port',
                description='Port for Miniserver HTTP and WebSocket (7095)',
                required=False,
            ),
            ConfigEntry(
                key=CONF_UDP_PORT,
                type=ConfigEntryType.INTEGER,
                default_value=self.server_config.discovery_port,
                label='Discovery UDP Port',
                description='Port for Loxone UDP discovery (7070)',
                required=False,
            ),
            ConfigEntry(
                key=CONF_MAC_ADDRESS,
                type=ConfigEntryType.STRING,
                default_value=self.server_config.mac_address,
                label='MAC Address',
                description='Custom MAC address (leave empty for auto)',
                required=False,
            ),
            ConfigEntry(
                key=CONF_MINISERVER_SERIAL,
                type=ConfigEntryType.STRING,
                default_value=self.server_config.master_serial,
                label='Miniserver Serial',
                description='Serial number of paired Miniserver (e.g. 504F94101A6D or MS1A6D; auto-detected if empty)',
                required=False,
            ),
            ConfigEntry(
                key=CONF_MINISERVER_IP,
                type=ConfigEntryType.STRING,
                default_value=self.server_config.miniserver_ip,
                label='Miniserver IP Address',
                description='IP address of the Loxone Miniserver (default 192.168.1.107)',
                required=False,
            ),
        ]

        zone_configs = [
            (CONF_ZONE_1_PLAYER, "Stereo-Ausgang 1 (Zone 1)", "Music Assistant Player für Loxone Stereo-Ausgang 1 (Zone 1)"),
            (CONF_ZONE_2_PLAYER, "Stereo-Ausgang 2 (Zone 2)", "Music Assistant Player für Loxone Stereo-Ausgang 2 (Zone 2)"),
            (CONF_ZONE_3_PLAYER, "Stereo-Ausgang 3 (Zone 3)", "Music Assistant Player für Loxone Stereo-Ausgang 3 (Zone 3)"),
            (CONF_ZONE_4_PLAYER, "Stereo-Ausgang 4 (Zone 4)", "Music Assistant Player für Loxone Stereo-Ausgang 4 (Zone 4)"),
            (CONF_ZONE_5_PLAYER, "Stereo-Ausgang 5 (Extension 1 Ausg. 1)", "Music Assistant Player für Stereo-Ausgang 5 (Extension 1 Ausgang 1)"),
            (CONF_ZONE_6_PLAYER, "Stereo-Ausgang 6 (Extension 1 Ausg. 2)", "Music Assistant Player für Stereo-Ausgang 6 (Extension 1 Ausgang 2)"),
            (CONF_ZONE_7_PLAYER, "Stereo-Ausgang 7 (Extension 2 Ausg. 1)", "Music Assistant Player für Stereo-Ausgang 7 (Extension 2 Ausgang 1)"),
            (CONF_ZONE_8_PLAYER, "Stereo-Ausgang 8 (Extension 2 Ausg. 2)", "Music Assistant Player für Stereo-Ausgang 8 (Extension 2 Ausgang 2)"),
        ]

        for key, lbl, desc in zone_configs:
            entries.append(
                ConfigEntry(
                    key=key,
                    type=ConfigEntryType.STRING,
                    default_value="auto",
                    options=tuple(player_options),
                    label=lbl,
                    description=desc,
                    required=False,
                    category="Zone Mapping",
                )
            )

        return tuple(entries)

    async def loaded_in_mass(self) -> None:
        """Called when Music Assistant starts the provider."""
        LOGGER.info('Starting Loxone AudioServer provider in Music Assistant...')

        # Configure storage path from Mass if available
        if hasattr(self.mass, 'storage_path') and self.mass.storage_path:
            self.server_config.data_dir = Path(self.mass.storage_path) / "loxone"

        # Load user configuration overrides if available
        def _get_val(key: str) -> Any:
            val = None
            if hasattr(self, 'get_config_value'):
                try:
                    val = self.get_config_value(key)
                except Exception:
                    pass
            if val is None and hasattr(self, 'config') and hasattr(self.config, 'get_value'):
                try:
                    val = self.config.get_value(key)
                except Exception:
                    pass
            if isinstance(val, (str, int, float, bool)):
                return val
            return None

        if sname := _get_val(CONF_SERVER_NAME):
            self.server_config.server_name = str(sname)
        if lip := _get_val(CONF_LISTEN_IP):
            self.server_config.host = str(lip)
        if aport := _get_val(CONF_APP_PORT):
            self.server_config.app_port = int(aport)
        if mport := _get_val(CONF_MS_PORT):
            self.server_config.ms_port = int(mport)
        if uport := _get_val(CONF_UDP_PORT):
            self.server_config.discovery_port = int(uport)
        if mac := _get_val(CONF_MAC_ADDRESS):
            self.server_config.mac_address = str(mac).strip()
        if mserial := _get_val(CONF_MINISERVER_SERIAL):
            self.server_config.master_serial = str(mserial).replace(":", "").replace("-", "").strip().upper()
        if ms_ip := _get_val(CONF_MINISERVER_IP):
            self.server_config.miniserver_ip = str(ms_ip).strip()

        self._register_routes()
        self._sync_zones_from_mass()

        # Subscribe to Mass internal events
        if hasattr(self.mass, 'subscribe'):
            self._unsub = self.mass.subscribe(self._on_mass_event)

        # Start servers and discovery
        try:
            await self.discovery.start()
        except Exception as exc:
            LOGGER.error("Failed to start Loxone discovery service: %s", exc)

        try:
            await self.server.start()
        except Exception as exc:
            LOGGER.error("Failed to start Loxone server: %s", exc)

        LOGGER.info(
            'Loxone AudioServer emulator running inside Music Assistant on %s (Ports: UDP %d, TCP %d, TCP %d)!',
            self.server_config.host,
            self.server_config.discovery_port,
            self.server_config.app_port,
            self.server_config.ms_port,
        )

        if self.server_config.uuid and self.server_config.miniserver_ip:
            LOGGER.info(
                'Scheduling Miniserver startup notification on boot (UUID: %s, MS IP: %s)',
                self.server_config.uuid,
                self.server_config.miniserver_ip,
            )
            asyncio.create_task(self.cfg.notify_miniserver_startup())

    async def unload(self, is_removed: bool = False) -> None:
        """Called when Music Assistant stops or removes the provider."""
        if callable(self._unsub):
            self._unsub()
            self._unsub = None

        await self.server.stop()
        await self.discovery.stop()
        LOGGER.info('Loxone AudioServer provider stopped')

    def _sync_zones_from_mass(self) -> None:
        """Maps Music Assistant players to Loxone zones respecting plugin config."""
        players = []
        if hasattr(self.mass, 'players'):
            if hasattr(self.mass.players, '_players') and isinstance(self.mass.players._players, dict):
                players = list(self.mass.players._players.values())
            elif hasattr(self.mass.players, 'all') and isinstance(self.mass.players.all, (list, tuple)):
                players = list(self.mass.players.all)
            else:
                try:
                    players = list(self.mass.players)
                except Exception as exc:
                    LOGGER.warning('Error iterating mass.players: %s', exc)
                    players = []

        player_map: Dict[str, Any] = {}
        for p in players:
            pid = getattr(p, 'player_id', None)
            if pid:
                player_map[str(pid)] = p

        def _get_val(key: str) -> Any:
            val = None
            if hasattr(self, 'get_config_value'):
                try:
                    val = self.get_config_value(key)
                except Exception:
                    pass
            if val is None and hasattr(self, 'config') and hasattr(self.config, 'get_value'):
                try:
                    val = self.config.get_value(key)
                except Exception:
                    pass
            if isinstance(val, (str, int, float, bool)):
                return val
            return None

        new_zone_to_player: Dict[int, str] = {}
        new_player_to_zone: Dict[str, int] = {}
        assigned_pids: set[str] = set()

        # Step 1: Explicitly assigned zones (1..8)
        for zid in range(1, 9):
            cfg_val = _get_val(f"zone_{zid}_player")
            if cfg_val and cfg_val not in ("auto", "disabled", "none", ""):
                cfg_pid = str(cfg_val).strip()
                if cfg_pid in player_map:
                    new_zone_to_player[zid] = cfg_pid
                    new_player_to_zone[cfg_pid] = zid
                    assigned_pids.add(cfg_pid)
                    LOGGER.info("Configured Zone %d -> Player %s (%s)", zid, cfg_pid, getattr(player_map[cfg_pid], 'name', ''))
                else:
                    LOGGER.warning("Configured player %s for Zone %d not found in Music Assistant", cfg_pid, zid)

        # Step 2: Auto-assignment for unassigned zones (1..8)
        remaining_players = [
            p for pid, p in sorted(
                player_map.items(),
                key=lambda kv: (getattr(kv[1], 'display_name', None) or getattr(kv[1], 'name', '')).lower()
            )
            if pid not in assigned_pids
        ]
        rem_idx = 0
        for zid in range(1, 9):
            cfg_val = _get_val(f"zone_{zid}_player")
            if cfg_val in ("disabled", "none"):
                LOGGER.info("Zone %d is disabled in plugin config", zid)
                continue
            if zid not in new_zone_to_player and rem_idx < len(remaining_players):
                p = remaining_players[rem_idx]
                pid = str(getattr(p, 'player_id'))
                new_zone_to_player[zid] = pid
                new_player_to_zone[pid] = zid
                assigned_pids.add(pid)
                rem_idx += 1
                LOGGER.info("Auto-assigned Zone %d -> Player %s (%s)", zid, pid, getattr(p, 'name', ''))

        # Step 3: Any additional players beyond zone 8
        next_zid = 9
        while rem_idx < len(remaining_players):
            p = remaining_players[rem_idx]
            pid = str(getattr(p, 'player_id'))
            new_zone_to_player[next_zid] = pid
            new_player_to_zone[pid] = next_zid
            assigned_pids.add(pid)
            rem_idx += 1
            LOGGER.info("Extended Zone %d -> Player %s (%s)", next_zid, pid, getattr(p, 'name', ''))
            next_zid += 1

        self.zone_to_player = new_zone_to_player
        self.player_to_zone = new_player_to_zone

        # Step 4: Update zone states
        for zid, pid in self.zone_to_player.items():
            player = player_map.get(pid)
            if not player:
                continue
            name = getattr(player, 'display_name', None) or getattr(player, 'name', f'Zone {zid}')
            vol = getattr(player, 'volume_level', 30)
            if vol is None:
                vol = 30
            powered = getattr(player, 'powered', True)
            playback_state = str(getattr(player, 'playback_state', 'idle')).lower()
            mode = 'play' if 'play' in playback_state else 'stop'
            power = 'on' if powered else 'off'

            if zid in self.zone_states:
                state = self.zone_states[zid]
                state.name = name
                state.volume = int(vol)
                state.power = power
                state.mode = mode
            else:
                self.zone_states[zid] = LoxoneZoneState(
                    playerid=zid,
                    name=name,
                    volume=int(vol),
                    power=power,
                    mode=mode,
                )
            if self.notifier:
                self.notifier.notify_zone_state_changed(self.zone_states[zid])

        LOGGER.info('Mapped %d Music Assistant players to Loxone zones', len(self.zone_to_player))

    def _on_mass_event(self, event: Any) -> None:
        """Receives internal Mass events."""
        event_type = getattr(event, 'event', None)
        obj_id = getattr(event, 'object_id', None)
        data = getattr(event, 'data', None)

        if str(event_type) in ('player_updated', 'EventType.PLAYER_UPDATED') and obj_id in self.player_to_zone:
            zid = self.player_to_zone[obj_id]
            state = self.zone_states.get(zid)
            if state and data:
                state.volume = getattr(data, 'volume_level', state.volume) or state.volume
                state.power = 'on' if getattr(data, 'powered', True) else 'off'
                mode_str = str(getattr(data, 'playback_state', '')).lower()
                state.mode = 'play' if 'play' in mode_str else ('pause' if 'pause' in mode_str else 'stop')
                self.notifier.notify_zone_state_changed(state)
        elif str(event_type) in ('queue_updated', 'queue_time_updated', 'EventType.QUEUE_UPDATED', 'EventType.QUEUE_TIME_UPDATED') and obj_id in self.player_to_zone:
            zid = self.player_to_zone[obj_id]
            state = self.zone_states.get(zid)
            if state and data:
                cur = getattr(data, 'current_item', None)
                if cur:
                    state.title = getattr(cur, 'name', '') or state.title
                    state.duration = int(getattr(cur, 'duration', 0) or 0)
                    media_item = getattr(cur, 'media_item', None)
                    if media_item:
                        art = getattr(media_item, 'artists', None)
                        if art:
                            state.artist = getattr(art[0], 'name', '')
                        elif hasattr(media_item, 'artist'):
                            state.artist = getattr(media_item.artist, 'name', '') or str(media_item.artist)
                        if hasattr(media_item, 'album'):
                            state.album = getattr(media_item.album, 'name', '') or str(media_item.album)
                        if img := getattr(media_item, 'image', None):
                            state.coverurl = getattr(img, 'path', '') or str(img)
                if hasattr(data, 'elapsed_time') and data.elapsed_time is not None:
                    state.time = int(data.elapsed_time)
                self.notifier.notify_zone_state_changed(state)
        elif str(event_type) in ('player_added', 'player_removed', 'EventType.PLAYER_ADDED', 'EventType.PLAYER_REMOVED'):
            self._sync_zones_from_mass()

    def _register_routes(self) -> None:
        """Registers all Loxone command routes."""
        secure = SecureHandlers(self.server_config.clean_master_serial, config=self.server_config)
        def on_player_name_updated(zone_id: int, name: str) -> None:
            if zone_id in self.zone_states:
                self.zone_states[zone_id].name = name
                LOGGER.info("Updated zone %s name to %s", zone_id, name)
                if self.notifier:
                    self.notifier.notify_zone_state_changed(self.zone_states[zone_id])

        def get_extra_diagnostics() -> Dict[str, Any]:
            players_info = []
            if hasattr(self.mass, "players"):
                try:
                    for p in self.mass.players:
                        players_info.append({
                            "player_id": getattr(p, "player_id", None),
                            "name": getattr(p, "display_name", None) or getattr(p, "name", None),
                            "provider": getattr(p, "provider", None),
                            "type": str(getattr(p, "type", None)),
                            "available": getattr(p, "available", None),
                            "powered": getattr(p, "powered", None),
                            "playback_state": str(getattr(p, "playback_state", None)),
                            "volume": getattr(p, "volume_level", None),
                        })
                except Exception as e:
                    players_info.append({"error": str(e)})

            queues_info = []
            if hasattr(self.mass, "player_queues"):
                try:
                    for q in self.mass.player_queues:
                        queues_info.append({
                            "queue_id": getattr(q, "queue_id", None),
                            "display_name": getattr(q, "display_name", None),
                            "active": getattr(q, "active", None),
                            "state": str(getattr(q, "state", None)),
                            "current_index": getattr(q, "current_index", None),
                        })
                except Exception as e:
                    queues_info.append({"error": str(e)})

            return {
                "zone_to_player": dict(self.zone_to_player),
                "player_to_zone": dict(self.player_to_zone),
                "ma_players": players_info,
                "ma_queues": queues_info,
                "last_play_attempt": getattr(self, "last_play_attempt", None),
                "last_play_error": getattr(self, "last_play_error", None),
            }

        cfg = ConfigHandlers(
            self.server_config,
            self.crypto,
            self._apply_loxone_config,
            notifier=self.notifier,
            on_player_name_updated=on_player_name_updated,
            get_extra_diagnostics=get_extra_diagnostics,
        )
        self.cfg = cfg

        # Zone controls
        async def send_cmd(zid: int, action: str, param: Any) -> None:
            pid = self.zone_to_player.get(zid)
            if not pid or not hasattr(self.mass, 'players'):
                return
            if action in ('play', 'resume'):
                if hasattr(self.mass, 'players'):
                    await self.mass.players.cmd_power(pid, True)
                    await self.mass.players.cmd_play(pid)
            elif action == 'power_on':
                if hasattr(self.mass, 'players'):
                    await self.mass.players.cmd_power(pid, True)
            elif action == 'pause':
                if hasattr(self.mass, 'players'):
                    await self.mass.players.cmd_pause(pid)
            elif action == 'stop':
                if hasattr(self.mass, 'players'):
                    await self.mass.players.cmd_stop(pid)
            elif action == 'volume':
                if hasattr(self.mass, 'players'):
                    await self.mass.players.cmd_volume_set(pid, int(param))
            elif action in ('queueplus', 'next') and hasattr(self.mass, 'player_queues'):
                await self.mass.player_queues.cmd_next(pid)
            elif action in ('queueminus', 'previous') and hasattr(self.mass, 'player_queues'):
                await self.mass.player_queues.cmd_previous(pid)

        zone = ZoneHandlers(lambda zid: self.zone_states.get(zid), send_cmd)

        # Queue controls
        async def get_queue(zid: int, start: int, limit: int) -> Dict[str, Any]:
            pid = self.zone_to_player.get(zid)
            items: List[Dict[str, Any]] = []
            if pid and hasattr(self.mass, 'player_queues'):
                try:
                    q_items = await self.mass.player_queues.get_queue_items(pid, limit=limit, offset=start)
                    for item in q_items:
                        items.append({
                            'id': getattr(item, 'queue_item_id', '1'),
                            'title': getattr(item, 'name', 'Track'),
                            'artist': getattr(getattr(item, 'media_item', None), 'artist', ''),
                            'album': '',
                            'duration': int(getattr(item, 'duration', 0) or 0),
                        })
                except Exception as exc:
                    LOGGER.debug('Error getting queue items: %s', exc)
            return {'id': f'queue_{zid}', 'items': items, 'start': start, 'totalitems': len(items)}

        async def ctrl_queue(zid: int, cmd: str, param: Any) -> None:
            pid = self.zone_to_player.get(zid)
            if not pid or not hasattr(self.mass, 'player_queues'):
                return
            if cmd == 'clear':
                await self.mass.player_queues.cmd_clear(pid)
            elif cmd == 'play':
                await self.mass.player_queues.cmd_play_index(pid, int(param))

        queue = QueueHandlers(get_queue, ctrl_queue)

        # Favorites & Radio
        async def get_favs(zid: int, start: int, limit: int) -> Dict[str, Any]:
            return {'totalitems': 0, 'start': start, 'items': []}

        async def play_fav(zid: int, fid: str) -> None:
            pass

        async def cycle_fav(zid: int) -> None:
            pass

        def _get_item_cover_url(item: Any, fallback: str = "") -> str:
            """Resolves artwork URL for an item via Music Assistant helpers, metadata, or CDN assets."""
            if hasattr(self.mass, "get_media_item_image_url"):
                try:
                    url = self.mass.get_media_item_image_url(item)
                    if isinstance(url, str) and (url.startswith("http://") or url.startswith("https://") or url.startswith("/")):
                        return url
                except Exception:
                    pass

            img_path = ""
            if img := getattr(item, "image", None):
                if isinstance(img, str):
                    img_path = img
                elif hasattr(img, "path") and isinstance(img.path, str):
                    img_path = img.path
                elif hasattr(img, "url") and isinstance(img.url, str):
                    img_path = img.url
            elif hasattr(item, "metadata") and getattr(item.metadata, "images", None):
                images = item.metadata.images
                if isinstance(images, (list, tuple)) and images:
                    first_img = images[0]
                    if isinstance(first_img, str):
                        img_path = first_img
                    elif hasattr(first_img, "path") and isinstance(first_img.path, str):
                        img_path = first_img.path
                    elif hasattr(first_img, "url") and isinstance(first_img.url, str):
                        img_path = first_img.url
            elif hasattr(item, "album") and getattr(item.album, "image", None):
                alb_img = item.album.image
                if isinstance(alb_img, str):
                    img_path = alb_img
                elif hasattr(alb_img, "path") and isinstance(alb_img.path, str):
                    img_path = alb_img.path

            if img_path and isinstance(img_path, str):
                cand = img_path.strip()
                if cand.startswith("http://") or cand.startswith("https://"):
                    return cand
                base_url = getattr(self.mass, "server_url", None)
                if not base_url and hasattr(self.mass, "web") and hasattr(self.mass.web, "base_url"):
                    base_url = self.mass.web.base_url
                if isinstance(base_url, str) and base_url:
                    from urllib.parse import quote
                    return f"{str(base_url).rstrip('/')}/imageproxy?path={quote(cand)}"
                return cand

            # Branded high-resolution assets for Swiss/German stations
            name = getattr(item, "name", "")
            if isinstance(name, str):
                name_l = name.lower()
                if "srf 3" in name_l or "srf3" in name_l:
                    return f"{GITHUB_ASSETS_BASE}/srf3.png"
                if "srf 1" in name_l or "srf1" in name_l:
                    return f"{GITHUB_ASSETS_BASE}/srf1.png"
                if "swiss pop" in name_l or "swisspop" in name_l:
                    return f"{GITHUB_ASSETS_BASE}/swisspop.png"
                if "energy basel" in name_l:
                    return f"{GITHUB_ASSETS_BASE}/energy_basel.png"
                if "energy zürich" in name_l or "energy zuerich" in name_l:
                    return f"{GITHUB_ASSETS_BASE}/energy_zuerich.png"
                if "1live" in name_l:
                    return f"{GITHUB_ASSETS_BASE}/1live.png"
                if "swr3" in name_l:
                    return f"{GITHUB_ASSETS_BASE}/swr3.png"
                if "bayern 3" in name_l or "bayern3" in name_l:
                    return f"{GITHUB_ASSETS_BASE}/bayern3.png"

            return fallback

        async def get_radios() -> List[Dict[str, Any]]:
            radios: List[Dict[str, Any]] = []
            if hasattr(self.mass, 'music'):
                try:
                    items = []
                    if hasattr(self.mass.music, 'radios') and hasattr(self.mass.music.radios, 'library_items'):
                        res = self.mass.music.radios.library_items()
                        if asyncio.iscoroutine(res):
                            items = await res
                        elif hasattr(res, '__aiter__'):
                            items = [it async for it in res]
                        elif isinstance(res, (list, tuple)):
                            items = list(res)
                    elif hasattr(self.mass.music, 'get_library_radios'):
                        res = self.mass.music.get_library_radios()
                        if asyncio.iscoroutine(res):
                            items = await res
                        elif isinstance(res, (list, tuple)):
                            items = list(res)

                    for r in items:
                        item_id = str(getattr(r, 'item_id', len(radios) + 1))
                        name = getattr(r, 'name', 'Radio Station')
                        uri = getattr(r, 'uri', None) or f"library://radio/{item_id}"
                        stream_url = ""
                        if hasattr(r, 'provider_mappings') and r.provider_mappings:
                            for pm in r.provider_mappings:
                                if hasattr(pm, 'url') and pm.url and (pm.url.startswith('http://') or pm.url.startswith('https://')):
                                    stream_url = pm.url
                                    break
                        cover = _get_item_cover_url(r, fallback="")
                        is_fav = bool(getattr(r, 'favorite', False))

                        station_entry = {
                            'id': item_id,
                            'name': name,
                            'station': name,
                            'cover': cover,
                            'coverurl': cover,
                            'thumbnail': cover,
                            'audiopath': uri,
                            'stream_url': stream_url,
                            'favorite': is_fav,
                            'type': 2,
                        }
                        radios.append(station_entry)

                        # Register in _radio_catalog for quick metadata resolution when played
                        self._radio_catalog[item_id] = station_entry
                        self._radio_catalog[uri] = station_entry
                        if stream_url:
                            self._radio_catalog[stream_url] = station_entry
                        self._radio_catalog[name.lower()] = station_entry
                except Exception as exc:
                    LOGGER.debug('Error getting radios: %s', exc)

            # Sort favorites to top
            radios.sort(key=lambda x: (not x.get('favorite', False), x.get('name', '').lower()))

            # Register DEFAULT_RADIO_STATIONS in _radio_catalog as well
            for st in DEFAULT_RADIO_STATIONS:
                self._radio_catalog[st['id']] = st
                self._radio_catalog[st['audiopath']] = st
                self._radio_catalog[st['name'].lower()] = st

            return radios

        async def get_playlists(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
            playlists: List[Dict[str, Any]] = []
            if hasattr(self.mass, 'music'):
                try:
                    if hasattr(self.mass.music, 'playlists') and hasattr(self.mass.music.playlists, 'library_items'):
                        items = await self.mass.music.playlists.library_items(offset=offset, limit=limit)
                    elif hasattr(self.mass.music, 'get_library_playlists'):
                        items = await self.mass.music.get_library_playlists()
                    else:
                        items = []
                    for p in items:
                        item_id = str(getattr(p, 'item_id', len(playlists) + 1))
                        uri = getattr(p, 'uri', None) or f"library://playlist/{item_id}"
                        cover = _get_item_cover_url(p, fallback="")
                        playlists.append({
                            'id': item_id,
                            'name': getattr(p, 'name', 'Playlist'),
                            'totalitems': getattr(p, 'item_count', 0) or 0,
                            'cover': cover,
                            'audiopath': uri,
                        })
                except Exception as exc:
                    LOGGER.debug('Error getting playlists: %s', exc)
            return playlists

        async def get_albums(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
            albums: List[Dict[str, Any]] = []
            if hasattr(self.mass, 'music'):
                try:
                    if hasattr(self.mass.music, 'albums') and hasattr(self.mass.music.albums, 'library_items'):
                        items = await self.mass.music.albums.library_items(offset=offset, limit=limit)
                    else:
                        items = []
                    for a in items:
                        item_id = str(getattr(a, 'item_id', len(albums) + 1))
                        uri = getattr(a, 'uri', None) or f"library://album/{item_id}"
                        cover = _get_item_cover_url(a, fallback="")
                        artist_name = ""
                        if hasattr(a, 'artists') and a.artists:
                            artist_name = getattr(a.artists[0], 'name', '')
                        elif hasattr(a, 'artist'):
                            artist_name = getattr(a.artist, 'name', '') or str(a.artist)
                        albums.append({
                            'id': item_id,
                            'name': getattr(a, 'name', 'Album'),
                            'artist': artist_name,
                            'cover': cover,
                            'audiopath': uri,
                        })
                except Exception as exc:
                    LOGGER.debug('Error getting albums: %s', exc)
            return albums

        async def get_artists(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
            artists: List[Dict[str, Any]] = []
            if hasattr(self.mass, 'music'):
                try:
                    if hasattr(self.mass.music, 'artists') and hasattr(self.mass.music.artists, 'library_items'):
                        items = await self.mass.music.artists.library_items(offset=offset, limit=limit)
                    else:
                        items = []
                    for ar in items:
                        item_id = str(getattr(ar, 'item_id', len(artists) + 1))
                        uri = getattr(ar, 'uri', None) or f"library://artist/{item_id}"
                        cover = _get_item_cover_url(ar, fallback="")
                        artists.append({
                            'id': item_id,
                            'name': getattr(ar, 'name', 'Artist'),
                            'cover': cover,
                            'audiopath': uri,
                        })
                except Exception as exc:
                    LOGGER.debug('Error getting artists: %s', exc)
            return artists

        async def play_media(
            zid: int,
            media: str,
            parent: Optional[str] = None,
            shuffle: Optional[bool] = None,
        ) -> None:
            pid = self.zone_to_player.get(zid)
            if not pid or not hasattr(self.mass, "player_queues"):
                LOGGER.warning("play_media: zone %d has no mapped player or no player_queues", zid)
                self.last_play_error = {
                    "time": int(time.time()),
                    "zid": zid,
                    "error": f"Zone {zid} has no mapped player or no player_queues",
                }
                return

            target_media = media
            if target_media.startswith("folder:"):
                return
            if target_media.startswith("radio:"):
                target_media = f"library://radio/{target_media.split(':', 1)[1]}"
            elif target_media.startswith("playlist:"):
                target_media = f"library://playlist/{target_media.split(':', 1)[1]}"
            elif target_media.startswith("album:"):
                target_media = f"library://album/{target_media.split(':', 1)[1]}"
            elif target_media.startswith("artist:"):
                target_media = f"library://artist/{target_media.split(':', 1)[1]}"

            # Look up station metadata from _radio_catalog or DEFAULT_RADIO_STATIONS
            station_info = None
            if target_media in self._radio_catalog:
                station_info = self._radio_catalog[target_media]
            elif target_media.startswith("library://radio/"):
                rad_id = target_media.split("/")[-1]
                station_info = self._radio_catalog.get(rad_id)
            elif target_media.startswith("radio:"):
                rad_id = target_media.split(":", 1)[1]
                station_info = self._radio_catalog.get(rad_id)

            if not station_info:
                for station in DEFAULT_RADIO_STATIONS:
                    if (
                        target_media == station["id"]
                        or target_media == f"radio:{station['id']}"
                        or target_media == station["audiopath"]
                    ):
                        station_info = station
                        break

            if station_info and station_info.get("audiopath"):
                if station_info["audiopath"].startswith("http://") or station_info["audiopath"].startswith("https://"):
                    target_media = station_info["audiopath"]

            target_parent = parent
            if target_parent:
                if target_parent.startswith("album:"):
                    target_parent = f"library://album/{target_parent.split(':', 1)[1]}"
                elif target_parent.startswith("playlist:"):
                    target_parent = f"library://playlist/{target_parent.split(':', 1)[1]}"
                elif target_parent.startswith("artist:"):
                    target_parent = f"library://artist/{target_parent.split(':', 1)[1]}"

            # An artist should NEVER be passed as parent container because MA doesn't support start_item on artist
            if target_parent and (target_parent.startswith("library://artist/") or target_parent.startswith("artist:")):
                LOGGER.debug("Ignoring artist parent %s for track %s", target_parent, target_media)
                target_parent = None

            self.last_play_attempt = {
                "time": int(time.time()),
                "zid": zid,
                "pid": pid,
                "media": target_media,
                "parent": target_parent,
                "shuffle": shuffle,
            }

            try:
                # 1. Power on & unmute player
                if hasattr(self.mass, "players"):
                    try:
                        await self.mass.players.cmd_power(pid, True)
                    except Exception as p_err:
                        LOGGER.debug("cmd_power failed: %s", p_err)
                    try:
                        await self.mass.players.cmd_volume_mute(pid, False)
                    except Exception:
                        pass

                # 2. Update zone state immediately for responsive UI
                if zid in self.zone_states:
                    state = self.zone_states[zid]
                    state.power = "on"
                    state.mode = "play"
                    state.audiopath = target_media
                    if station_info:
                        state.station = station_info.get("station") or station_info.get("name", "")
                        state.title = station_info.get("name", "")
                        state.artist = "Radio"
                        state.album = ""
                        state.coverurl = station_info.get("cover") or station_info.get("coverurl", "")
                        state.audiotype = 2
                        state.type = 2
                    if self.notifier:
                        self.notifier.notify_zone_state_changed(state)

                kwargs: Dict[str, Any] = {}
                if shuffle is not None:
                    kwargs["shuffle"] = shuffle

                played = False
                if target_parent:
                    try:
                        LOGGER.info(
                            "Attempting play_media on queue %s with parent %s and start_item %s (option=replace)",
                            pid,
                            target_parent,
                            target_media,
                        )
                        await self.mass.player_queues.play_media(
                            pid,
                            media=target_parent,
                            start_item=target_media,
                            option="replace",
                            **kwargs,
                        )
                        played = True
                    except Exception as parent_exc:
                        LOGGER.warning(
                            "Could not play parent %s with start_item %s: %s; falling back to direct media",
                            target_parent,
                            target_media,
                            parent_exc,
                        )

                if not played:
                    LOGGER.info(
                        "Attempting play_media on queue %s with media %s (option=replace)",
                        pid,
                        target_media,
                    )
                    await self.mass.player_queues.play_media(
                        pid,
                        media=target_media,
                        option="replace",
                        **kwargs,
                    )

                self.last_play_error = None
            except Exception as exc:
                import traceback
                tb = traceback.format_exc()
                LOGGER.error("Failed to play media %s on zone %d: %s\n%s", target_media, zid, exc, tb)
                self.last_play_error = {
                    "time": int(time.time()),
                    "zid": zid,
                    "pid": pid,
                    "media": target_media,
                    "error": str(exc),
                    "traceback": tb,
                }

        def _format_track_item(t: Any, fallback_id: Any = "1") -> Dict[str, Any]:
            tid = str(getattr(t, "item_id", None) or fallback_id)
            name = getattr(t, "name", None) or getattr(t, "title", "Track")
            artist_name = ""
            if hasattr(t, "artists") and t.artists:
                artist_name = getattr(t.artists[0], "name", "")
            elif hasattr(t, "artist"):
                artist_name = getattr(t.artist, "name", "") or str(t.artist or "")

            album_name = ""
            if hasattr(t, "album") and t.album:
                album_name = getattr(t.album, "name", "") or str(t.album or "")

            img_path = _get_item_cover_url(t, fallback="")

            uri = getattr(t, "uri", None) or f"library://track/{tid}"

            return {
                "id": tid,
                "name": name,
                "title": name,
                "artist": artist_name,
                "album": album_name,
                "duration": int(getattr(t, "duration", 0) or 0),
                "audiopath": uri,
                "cover": img_path,
                "coverurl": img_path,
                "thumbnail": img_path,
                "tag": "track",
                "type": 2,
            }

        async def get_album_tracks(album_id: str) -> List[Dict[str, Any]]:
            tracks: List[Dict[str, Any]] = []
            if hasattr(self.mass, "music") and hasattr(self.mass.music, "albums"):
                try:
                    items = []
                    if hasattr(self.mass.music.albums, "get_library_album_tracks"):
                        res = self.mass.music.albums.get_library_album_tracks(album_id)
                        if asyncio.iscoroutine(res):
                            items = await res
                        elif isinstance(res, (list, tuple)):
                            items = list(res)
                    if not items and hasattr(self.mass.music.albums, "tracks"):
                        res = self.mass.music.albums.tracks(album_id, "library")
                        if asyncio.iscoroutine(res):
                            items = await res
                        elif hasattr(res, "__aiter__"):
                            items = [it async for it in res]
                        elif isinstance(res, (list, tuple)):
                            items = list(res)
                    for t in items:
                        tracks.append(_format_track_item(t, len(tracks) + 1))
                except Exception as exc:
                    LOGGER.debug("Error getting album tracks for %s: %s", album_id, exc)
            return tracks

        async def get_artist_tracks(artist_id: str) -> List[Dict[str, Any]]:
            tracks: List[Dict[str, Any]] = []
            if hasattr(self.mass, "music") and hasattr(self.mass.music, "artists"):
                try:
                    items = []
                    if hasattr(self.mass.music.artists, "get_library_artist_toptracks"):
                        res = self.mass.music.artists.get_library_artist_toptracks(artist_id)
                        if asyncio.iscoroutine(res):
                            items = await res
                        elif isinstance(res, (list, tuple)):
                            items = list(res)
                    if not items and hasattr(self.mass.music.artists, "tracks"):
                        res = self.mass.music.artists.tracks(artist_id, "library")
                        if asyncio.iscoroutine(res):
                            items = await res
                        elif hasattr(res, "__aiter__"):
                            items = [it async for it in res]
                        elif isinstance(res, (list, tuple)):
                            items = list(res)
                    for t in items:
                        tracks.append(_format_track_item(t, len(tracks) + 1))
                except Exception as exc:
                    LOGGER.debug("Error getting artist tracks for %s: %s", artist_id, exc)
            return tracks

        async def get_playlist_tracks(playlist_id: str) -> List[Dict[str, Any]]:
            tracks: List[Dict[str, Any]] = []
            if playlist_id in ("favorites", "favorite", "4"):
                if hasattr(self.mass, "music") and hasattr(self.mass.music, "tracks"):
                    try:
                        items = await self.mass.music.tracks.library_items(favorite=True, limit=50)
                        for t in items:
                            tracks.append(_format_track_item(t, len(tracks) + 1))
                        return tracks
                    except Exception as exc:
                        LOGGER.debug("Error getting favorite tracks: %s", exc)
            if hasattr(self.mass, "music") and hasattr(self.mass.music, "playlists"):
                try:
                    items = []
                    if hasattr(self.mass.music.playlists, "tracks"):
                        res = self.mass.music.playlists.tracks(playlist_id, "library")
                        if asyncio.iscoroutine(res):
                            items = await res
                        elif hasattr(res, "__aiter__"):
                            items = [it async for it in res]
                        elif isinstance(res, (list, tuple)):
                            items = list(res)
                    for t in items:
                        tracks.append(_format_track_item(t, len(tracks) + 1))
                except Exception as exc:
                    LOGGER.debug("Error getting playlist tracks for %s: %s", playlist_id, exc)
            return tracks

        favs = FavoriteHandlers(
            get_favs,
            play_fav,
            cycle_fav,
            get_radios,
            get_playlists,
            play_media,
            get_albums=get_albums,
            get_artists=get_artists,
            get_playlist_tracks=get_playlist_tracks,
            get_album_tracks=get_album_tracks,
            get_artist_tracks=get_artist_tracks,
        )

        # Alerts & TTS
        async def play_tts(zids: List[int], text: str, lang: Optional[str], vol: Optional[int]) -> None:
            for zid in zids:
                pid = self.zone_to_player.get(zid)
                if pid and hasattr(self.mass, 'players'):
                    await self.mass.players.cmd_play_announcement(pid, url=f'tts://{text}')

        async def play_alert(zids: List[int], alert_type: str, vol: Optional[int]) -> None:
            for zid in zids:
                pid = self.zone_to_player.get(zid)
                if pid and hasattr(self.mass, 'players'):
                    await self.mass.players.cmd_play_announcement(pid, url=f'alert://{alert_type}')

        alerts = AlertHandlers(play_tts, play_alert)

        # Register prefixes
        self.router.register_prefix('secure/hello', secure.hello)
        self.router.register_prefix('secure/info/pairing', secure.info_pairing)
        self.router.register_prefix('secure/infopairing', secure.info_pairing)
        self.router.register_prefix('secure/authenticate', secure.authenticate)
        self.router.register_prefix('secure/init', secure.init)

        self.router.register_prefix('audio/cfg/getkey/full', cfg.get_key_full)
        self.router.register_prefix('audio/cfg/getkey', cfg.get_key)
        self.router.register_prefix('audio/cfg/getconfig', cfg.get_config)
        self.router.register_prefix('audio/cfg/setconfigtimestamp', cfg.set_config_timestamp)
        self.router.register_prefix('audio/cfg/setconfig', cfg.set_config)
        self.router.register_prefix('audio/cfg/ready', cfg.ready)
        self.router.register_prefix('audio/cfg/miniservertime', cfg.miniserver_time)
        self.router.register_prefix('audio/cfg/identify', cfg.identify)
        self.router.register_prefix('audio/cfg/diagnosis', cfg.diagnosis)
        self.router.register_prefix('audio/cfg/restart', cfg.restart)
        self.router.register_prefix('audio/cfg/devicestartup', cfg.trigger_startup)
        self.router.register_prefix('audio/cfg/trigger_startup', cfg.trigger_startup)
        self.router.register_prefix('audio/cfg/playername', cfg.player_name)
        self.router.register_prefix('audio/cfg/volumes', cfg.volumes)
        self.router.register_prefix('audio/cfg/getinputs', cfg.get_inputs)
        self.router.register_prefix('audio/cfg/all', cfg.get_all)

        self.router.register_prefix('audio/cfg/getservices', favs.get_services)
        self.router.register_prefix('audio/cfg/getavailableservices', favs.get_available_services)
        self.router.register_prefix('audio/cfg/getservicefolder', favs.get_service_folder)
        self.router.register_prefix('audio/cfg/globalsearch/describe', favs.global_search_describe)
        self.router.register_prefix('audio/cfg/globalsearch', favs.global_search)
        self.router.register_prefix('audio/cfg/getradios', favs.get_radios)
        self.router.register_prefix('audio/cfg/getplaylists2', favs.get_playlists)
        self.router.register_prefix('audio/cfg/getroomfavs', favs.get_room_favs)
        self.router.register_prefix('audio/cfg/roomfavs', favs.room_favs_action)

        # Register Regexes
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/status/?$'), zone.get_status)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/recent'), zone.get_recent)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/geteq'), zone.get_eq)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/equalizer'), zone.equalizer)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/getqueue'), queue.get_queue)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/queue/clear'), queue.queue_clear)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/queue/play/'), queue.queue_play)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/queueandplay/'), queue.queue_and_play)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/queueadd/'), queue.queue_add)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/serviceplay/'), favs.service_play)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/playurl/'), favs.play_url)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/roomfav/play/'), favs.room_fav_play)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/roomfav/plus/?$'), favs.room_fav_plus)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/tts/'), alerts.zone_tts)
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/(?:bell|alarm|firealarm|clock|buzzer)'), alerts.zone_alert)
        self.router.register_regex(re.compile(r'^audio/grouped/'), alerts.grouped_alert)
        self.router.register_regex(
            re.compile(r'^audio/(?:cfg/)?\d+/(?:on|off|play|pause|resume|position|volume|queueplus|queueminus|repeat|shuffle)'),
            zone.dynamic_command,
        )

    def _apply_loxone_config(self, raw_cfg: Any) -> None:
        """Parses deployed Loxone config to extract master pairing and UUID information."""
        mac_needle = self.server_config.clean_mac
        entries = raw_cfg if isinstance(raw_cfg, list) else ([raw_cfg] if isinstance(raw_cfg, dict) else [])
        for entry in entries:
            if isinstance(entry, dict):
                for k, v in entry.items():
                    if k.replace(":", "").replace("-", "").upper() == mac_needle and isinstance(v, dict):
                        master = v.get("master")
                        if master:
                            clean_m = str(master).replace(":", "").replace("-", "").upper()
                            self.server_config.master_serial = clean_m
                            if hasattr(self, "secure"):
                                self.secure.set_master(clean_m)
                        if uuid_val := v.get("uuid"):
                            self.server_config.uuid = str(uuid_val).strip()
                        if ip_val := (v.get("ip") or v.get("host_ip")):
                            if "." in str(ip_val):
                                self.server_config.miniserver_ip = str(ip_val).strip()
                        if name_val := v.get("name"):
                            self.server_config.server_name = str(name_val).strip()
                        LOGGER.info(
                            "Applied Loxone config: uuid=%s, ms_ip=%s, master=%s, name=%s",
                            self.server_config.uuid,
                            self.server_config.miniserver_ip,
                            self.server_config.master_serial,
                            self.server_config.server_name,
                        )
