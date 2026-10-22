"""Music Assistant Native Plugin Provider for Loxone AudioServer Emulation."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .config import ServerConfig
from .crypto import LoxoneCrypto
from .discovery import LoxoneDiscoveryService
from .handlers.alert_handlers import AlertHandlers
from .handlers.config_handlers import ConfigHandlers
from .handlers.favorite_handlers import FavoriteHandlers
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

    class ConfigEntryType:  # type: ignore[no-redef]
        STRING = 'string'
        INTEGER = 'integer'
        BOOLEAN = 'boolean'

LOGGER = logging.getLogger('music_assistant.providers.loxone')

CONF_SERVER_NAME = 'server_name'
CONF_LISTEN_IP = 'listen_ip'
CONF_APP_PORT = 'app_http_port'
CONF_MS_PORT = 'ms_http_port'
CONF_UDP_PORT = 'udp_discovery_port'
CONF_MAC_ADDRESS = 'mac_address'


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

        self.notifier = LoxoneWebSocketNotifier(
            self.server_config,
            get_all_states=lambda: list(self.zone_states.values()),
        )
        self.discovery = LoxoneDiscoveryService(self.server_config)
        self.server = LoxoneServer(self.server_config, self.router, self.notifier)
        self._unsub: Optional[Any] = None

    async def get_config_entries(self) -> tuple[Any, ...]:
        """Return Config entries to configure this provider in Music Assistant UI."""
        return (
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
        )

    async def loaded_in_mass(self) -> None:
        """Called when Music Assistant starts the provider."""
        LOGGER.info('Starting Loxone AudioServer provider in Music Assistant...')

        # Load user configuration overrides if available
        def _get_val(key: str) -> Any:
            if hasattr(self, 'get_config_value'):
                val = self.get_config_value(key)
                if val is not None:
                    return val
            if hasattr(self, 'config') and hasattr(self.config, 'get_value'):
                val = self.config.get_value(key)
                if val is not None:
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
            self.server_config.mac_address = str(mac)

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

    async def unload(self, is_removed: bool = False) -> None:
        """Called when Music Assistant stops or removes the provider."""
        if callable(self._unsub):
            self._unsub()
            self._unsub = None

        await self.server.stop()
        await self.discovery.stop()
        LOGGER.info('Loxone AudioServer provider stopped')

    def _sync_zones_from_mass(self) -> None:
        """Maps Music Assistant players directly to Loxone zones."""
        zone_id = 1
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

        for player in players:
            pid = getattr(player, 'player_id', None)
            if not pid:
                continue
            self.zone_to_player[zone_id] = pid
            self.player_to_zone[pid] = zone_id

            name = getattr(player, 'display_name', None) or getattr(player, 'name', f'Zone {zone_id}')
            vol = getattr(player, 'volume_level', 30)
            if vol is None:
                vol = 30
            powered = getattr(player, 'powered', True)
            playback_state = str(getattr(player, 'playback_state', 'idle')).lower()
            mode = 'play' if 'play' in playback_state else 'stop'
            power = 'on' if powered else 'off'

            self.zone_states[zone_id] = LoxoneZoneState(
                playerid=zone_id,
                name=name,
                volume=int(vol),
                power=power,
                mode=mode,
            )
            zone_id += 1
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
        elif str(event_type) in ('player_added', 'player_removed', 'EventType.PLAYER_ADDED', 'EventType.PLAYER_REMOVED'):
            self._sync_zones_from_mass()

    def _register_routes(self) -> None:
        """Registers all Loxone command routes."""
        secure = SecureHandlers()
        cfg = ConfigHandlers(self.server_config, self.crypto, self._apply_loxone_config)

        # Zone controls
        async def send_cmd(zid: int, action: str, param: Any) -> None:
            pid = self.zone_to_player.get(zid)
            if not pid or not hasattr(self.mass, 'players'):
                return
            if action == 'play':
                await self.mass.players.cmd_play(pid)
            elif action == 'pause':
                await self.mass.players.cmd_pause(pid)
            elif action == 'stop':
                await self.mass.players.cmd_stop(pid)
            elif action == 'volume':
                await self.mass.players.cmd_volume_set(pid, int(param))
            elif action == 'queueplus' and hasattr(self.mass, 'player_queues'):
                await self.mass.player_queues.cmd_next(pid)
            elif action == 'queueminus' and hasattr(self.mass, 'player_queues'):
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

        async def get_radios() -> List[Dict[str, Any]]:
            radios: List[Dict[str, Any]] = []
            if hasattr(self.mass, 'music'):
                try:
                    items = await self.mass.music.get_library_radios()
                    for r in items:
                        radios.append({
                            'id': str(getattr(r, 'item_id', len(radios) + 1)),
                            'name': getattr(r, 'name', 'Radio Station'),
                            'cover': getattr(getattr(r, 'image', None), 'path', ''),
                            'station': getattr(r, 'name', ''),
                        })
                except Exception as exc:
                    LOGGER.debug('Error getting radios: %s', exc)
            return radios

        async def get_playlists(start: int, limit: int) -> List[Dict[str, Any]]:
            playlists: List[Dict[str, Any]] = []
            if hasattr(self.mass, 'music'):
                try:
                    items = await self.mass.music.get_library_playlists()
                    for p in items:
                        playlists.append({
                            'id': str(getattr(p, 'item_id', len(playlists) + 1)),
                            'name': getattr(p, 'name', 'Playlist'),
                            'totalitems': getattr(p, 'item_count', 0) or 0,
                        })
                except Exception as exc:
                    LOGGER.debug('Error getting playlists: %s', exc)
            return playlists

        async def play_media(zid: int, media: str) -> None:
            pid = self.zone_to_player.get(zid)
            if pid and hasattr(self.mass, 'player_queues'):
                await self.mass.player_queues.play_media(pid, media)

        favs = FavoriteHandlers(get_favs, play_fav, cycle_fav, get_radios, get_playlists, play_media)

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

        self.router.register_prefix('audio/cfg/getservices', favs.get_services)
        self.router.register_prefix('audio/cfg/getavailableservices', favs.get_available_services)
        self.router.register_prefix('audio/cfg/getradios', favs.get_radios)
        self.router.register_prefix('audio/cfg/getplaylists2', favs.get_playlists)
        self.router.register_prefix('audio/cfg/getroomfavs', favs.get_room_favs)
        self.router.register_prefix('audio/cfg/roomfavs', favs.room_favs_action)

        # Register Regexes
        self.router.register_regex(re.compile(r'^audio/(?:cfg/)?\d+/status/?$'), zone.get_status)
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
        pass
