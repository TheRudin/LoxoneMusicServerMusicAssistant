"""Music Assistant Native Plugin Provider for Loxone AudioServer Emulation."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from ..config import ServerConfig
from ..loxone.crypto import LoxoneCrypto
from ..loxone.discovery import LoxoneDiscoveryService
from ..loxone.handlers.alert_handlers import AlertHandlers
from ..loxone.handlers.config_handlers import ConfigHandlers
from ..loxone.handlers.favorite_handlers import FavoriteHandlers
from ..loxone.handlers.queue_handlers import QueueHandlers
from ..loxone.handlers.secure_handlers import SecureHandlers
from ..loxone.handlers.zone_handlers import ZoneHandlers
from ..loxone.router import LoxoneRouter
from ..loxone.server import LoxoneServer
from ..loxone.websocket import LoxoneWebSocketNotifier, LoxoneZoneState

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ConfigEntry, ProviderConfig
    from music_assistant_models.event import MassEvent
    from music_assistant_models.provider import ProviderManifest
    from music_assistant.mass import MusicAssistant

try:
    from music_assistant.models.plugin import PluginProvider
except ImportError:
    # Standalone fallback when running outside Music Assistant
    class PluginProvider:  # type: ignore[no-redef]
        def __init__(self, mass: Any, manifest: Any, config: Any) -> None:
            self.mass = mass
            self.manifest = manifest
            self.config = config

logger = logging.getLogger("music_assistant.providers.loxone")


class LoxoneMusicServerPlugin(PluginProvider):
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
            router=self.router,
        )
        self.discovery = LoxoneDiscoveryService(self.server_config)
        self.server = LoxoneServer(self.server_config, self.router, self.notifier)

    async def loaded_in_mass(self) -> None:
        """Called when Music Assistant starts the provider."""
        logger.info("Initializing Loxone AudioServer provider in Music Assistant...")

        self._register_routes()
        self._sync_zones_from_mass()

        # Subscribe to Mass internal events
        if hasattr(self.mass, "subscribe"):
            self.mass.subscribe(self._on_mass_event)

        # Start servers and discovery
        await self.discovery.start()
        await self.server.start()
        logger.info("Loxone AudioServer emulator running inside Music Assistant!")

    async def unload(self, is_removed: bool = False) -> None:
        """Called when Music Assistant stops or removes the provider."""
        await self.server.stop()
        await self.discovery.stop()
        logger.info("Loxone AudioServer provider stopped")

    def _sync_zones_from_mass(self) -> None:
        """Maps Music Assistant players directly to Loxone zones."""
        zone_id = 1
        players = []
        if hasattr(self.mass, "players"):
            if hasattr(self.mass.players, "_players") and isinstance(self.mass.players._players, dict):
                players = list(self.mass.players._players.values())
            elif hasattr(self.mass.players, "all") and isinstance(self.mass.players.all, (list, tuple)):
                players = list(self.mass.players.all)
            else:
                try:
                    players = list(self.mass.players)
                except Exception as exc:
                    logger.warning("Error iterating mass.players: %s", exc)
                    players = []

        for player in players:
            pid = getattr(player, "player_id", None)
            if not pid:
                continue
            self.zone_to_player[zone_id] = pid
            self.player_to_zone[pid] = zone_id

            name = getattr(player, "display_name", None) or getattr(player, "name", f"Zone {zone_id}")
            vol = getattr(player, "volume_level", 30)
            if vol is None:
                vol = 30
            powered = getattr(player, "powered", True)
            playback_state = str(getattr(player, "playback_state", "idle")).lower()
            mode = "play" if "play" in playback_state else "stop"
            power = "on" if powered else "off"

            self.zone_states[zone_id] = LoxoneZoneState(
                playerid=zone_id,
                name=name,
                volume=int(vol),
                power=power,
                mode=mode,
            )
            zone_id += 1

    def _on_mass_event(self, event: Any) -> None:
        """Receives internal Mass events."""
        event_type = getattr(event, "event", None)
        obj_id = getattr(event, "object_id", None)
        data = getattr(event, "data", None)

        if str(event_type) in ("player_updated", "EventType.PLAYER_UPDATED") and obj_id in self.player_to_zone:
            zid = self.player_to_zone[obj_id]
            state = self.zone_states.get(zid)
            if state and data:
                state.volume = getattr(data, "volume_level", state.volume) or state.volume
                state.power = "on" if getattr(data, "powered", True) else "off"
                mode_str = str(getattr(data, "playback_state", "")).lower()
                state.mode = "play" if "play" in mode_str else ("pause" if "pause" in mode_str else "stop")
                self.notifier.notify_zone_state_changed(state)
        elif str(event_type) in ("player_added", "player_removed", "EventType.PLAYER_ADDED", "EventType.PLAYER_REMOVED"):
            self._sync_zones_from_mass()

    def _register_routes(self) -> None:
        """Registers all Loxone command routes."""
        def on_player_name_updated(zone_id: int, name: str) -> None:
            if zone_id in self.zone_states:
                self.zone_states[zone_id].name = name
                LOGGER.info("Updated zone %s name to %s", zone_id, name)
                if self.notifier:
                    self.notifier.notify_zone_state_changed(self.zone_states[zone_id])

        cfg = ConfigHandlers(
            self.server_config,
            self.crypto,
            self._apply_loxone_config,
            notifier=self.notifier,
            on_player_name_updated=on_player_name_updated,
        )
        self.cfg = cfg

        # Zone controls
        async def send_cmd(zid: int, action: str, param: Any) -> None:
            pid = self.zone_to_player.get(zid)
            if not pid or not hasattr(self.mass, "players"):
                return
            if action == "play":
                await self.mass.players.cmd_play(pid)
            elif action == "pause":
                await self.mass.players.cmd_pause(pid)
            elif action == "stop":
                await self.mass.players.cmd_stop(pid)
            elif action == "volume":
                await self.mass.players.cmd_volume_set(pid, int(param))

        zone = ZoneHandlers(lambda zid: self.zone_states.get(zid), send_cmd)

        # Queue controls
        async def get_queue(zid: int, start: int, limit: int) -> Dict[str, Any]:
            return {"id": f"queue_{zid}", "items": [], "start": start, "totalitems": 0}

        async def ctrl_queue(zid: int, cmd: str, param: Any) -> None:
            pass

        queue = QueueHandlers(get_queue, ctrl_queue)

        # Favorites & Radio
        async def get_favs(zid: int, start: int, limit: int) -> Dict[str, Any]:
            return {"totalitems": 0, "start": start, "items": []}

        async def play_fav(zid: int, fid: str) -> None:
            pass

        async def cycle_fav(zid: int) -> None:
            pass

        async def get_radios() -> List[Dict[str, Any]]:
            radios: List[Dict[str, Any]] = []
            if hasattr(self.mass, "music"):
                try:
                    if hasattr(self.mass.music, "radios") and hasattr(self.mass.music.radios, "library_items"):
                        items = await self.mass.music.radios.library_items()
                    elif hasattr(self.mass.music, "get_library_radios"):
                        items = await self.mass.music.get_library_radios()
                    else:
                        items = []
                    for r in items:
                        item_id = str(getattr(r, "item_id", len(radios) + 1))
                        uri = getattr(r, "uri", None) or f"library://radio/{item_id}"
                        cover = ""
                        if img := getattr(r, "image", None):
                            cover = getattr(img, "path", "") or str(img)
                        elif hasattr(r, "metadata") and getattr(r.metadata, "images", None):
                            cover = r.metadata.images[0].path if r.metadata.images else ""
                        radios.append({
                            "id": item_id,
                            "name": getattr(r, "name", "Radio Station"),
                            "cover": cover,
                            "station": getattr(r, "name", ""),
                            "audiopath": uri,
                        })
                except Exception as exc:
                    LOGGER.debug("Error getting radios: %s", exc)
            return radios

        async def get_playlists(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
            playlists: List[Dict[str, Any]] = []
            if hasattr(self.mass, "music"):
                try:
                    if hasattr(self.mass.music, "playlists") and hasattr(self.mass.music.playlists, "library_items"):
                        items = await self.mass.music.playlists.library_items(offset=offset, limit=limit)
                    elif hasattr(self.mass.music, "get_library_playlists"):
                        items = await self.mass.music.get_library_playlists()
                    else:
                        items = []
                    for p in items:
                        item_id = str(getattr(p, "item_id", len(playlists) + 1))
                        uri = getattr(p, "uri", None) or f"library://playlist/{item_id}"
                        cover = ""
                        if img := getattr(p, "image", None):
                            cover = getattr(img, "path", "") or str(img)
                        elif hasattr(p, "metadata") and getattr(p.metadata, "images", None):
                            cover = p.metadata.images[0].path if p.metadata.images else ""
                        playlists.append({
                            "id": item_id,
                            "name": getattr(p, "name", "Playlist"),
                            "totalitems": getattr(p, "item_count", 0) or 0,
                            "cover": cover,
                            "audiopath": uri,
                        })
                except Exception as exc:
                    LOGGER.debug("Error getting playlists: %s", exc)
            return playlists

        async def get_albums(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
            albums: List[Dict[str, Any]] = []
            if hasattr(self.mass, "music"):
                try:
                    if hasattr(self.mass.music, "albums") and hasattr(self.mass.music.albums, "library_items"):
                        items = await self.mass.music.albums.library_items(offset=offset, limit=limit)
                    else:
                        items = []
                    for a in items:
                        item_id = str(getattr(a, "item_id", len(albums) + 1))
                        uri = getattr(a, "uri", None) or f"library://album/{item_id}"
                        cover = ""
                        if img := getattr(a, "image", None):
                            cover = getattr(img, "path", "") or str(img)
                        elif hasattr(a, "metadata") and getattr(a.metadata, "images", None):
                            cover = a.metadata.images[0].path if a.metadata.images else ""
                        artist_name = ""
                        if hasattr(a, "artists") and a.artists:
                            artist_name = getattr(a.artists[0], "name", "")
                        elif hasattr(a, "artist"):
                            artist_name = getattr(a.artist, "name", "") or str(a.artist)
                        albums.append({
                            "id": item_id,
                            "name": getattr(a, "name", "Album"),
                            "artist": artist_name,
                            "cover": cover,
                            "audiopath": uri,
                        })
                except Exception as exc:
                    LOGGER.debug("Error getting albums: %s", exc)
            return albums

        async def get_artists(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
            artists: List[Dict[str, Any]] = []
            if hasattr(self.mass, "music"):
                try:
                    if hasattr(self.mass.music, "artists") and hasattr(self.mass.music.artists, "library_items"):
                        items = await self.mass.music.artists.library_items(offset=offset, limit=limit)
                    else:
                        items = []
                    for ar in items:
                        item_id = str(getattr(ar, "item_id", len(artists) + 1))
                        uri = getattr(ar, "uri", None) or f"library://artist/{item_id}"
                        cover = ""
                        if img := getattr(ar, "image", None):
                            cover = getattr(img, "path", "") or str(img)
                        elif hasattr(ar, "metadata") and getattr(ar.metadata, "images", None):
                            cover = ar.metadata.images[0].path if ar.metadata.images else ""
                        artists.append({
                            "id": item_id,
                            "name": getattr(ar, "name", "Artist"),
                            "cover": cover,
                            "audiopath": uri,
                        })
                except Exception as exc:
                    LOGGER.debug("Error getting artists: %s", exc)
            return artists

        async def play_media(zid: int, media: str) -> None:
            pid = self.zone_to_player.get(zid)
            if not pid or not hasattr(self.mass, "player_queues"):
                return
            target_media = media
            if target_media.startswith("radio:"):
                target_media = f"library://radio/{target_media.split(':', 1)[1]}"
            elif target_media.startswith("playlist:"):
                target_media = f"library://playlist/{target_media.split(':', 1)[1]}"
            elif target_media.startswith("album:"):
                target_media = f"library://album/{target_media.split(':', 1)[1]}"
            elif target_media.startswith("artist:"):
                target_media = f"library://artist/{target_media.split(':', 1)[1]}"
            try:
                await self.mass.player_queues.play_media(pid, target_media)
            except Exception as exc:
                LOGGER.error("Failed to play media %s on zone %d: %s", target_media, zid, exc)

        favs = FavoriteHandlers(
            get_favs,
            play_fav,
            cycle_fav,
            get_radios,
            get_playlists,
            play_media,
            get_albums=get_albums,
            get_artists=get_artists,
        )

        # Alerts & TTS
        async def play_tts(zids: List[int], text: str, lang: Optional[str], vol: Optional[int]) -> None:
            for zid in zids:
                pid = self.zone_to_player.get(zid)
                if pid and hasattr(self.mass, "players"):
                    await self.mass.players.cmd_play_announcement(pid, url=f"tts://{text}")

        async def play_alert(zids: List[int], alert_type: str, vol: Optional[int]) -> None:
            pass

        alerts = AlertHandlers(play_tts, play_alert)

        # Register prefixes
        self.router.register_prefix("secure/hello", secure.hello)
        self.router.register_prefix("secure/info/pairing", secure.info_pairing)
        self.router.register_prefix("secure/infopairing", secure.info_pairing)
        self.router.register_prefix("secure/authenticate", secure.authenticate)
        self.router.register_prefix("secure/init", secure.init)

        self.router.register_prefix("audio/cfg/getkey/full", cfg.get_key_full)
        self.router.register_prefix("audio/cfg/getkey", cfg.get_key)
        self.router.register_prefix("audio/cfg/getconfig", cfg.get_config)
        self.router.register_prefix("audio/cfg/setconfigtimestamp", cfg.set_config_timestamp)
        self.router.register_prefix("audio/cfg/setconfig", cfg.set_config)
        self.router.register_prefix("audio/cfg/ready", cfg.ready)
        self.router.register_prefix("audio/cfg/miniservertime", cfg.miniserver_time)
        self.router.register_prefix("audio/cfg/identify", cfg.identify)
        self.router.register_prefix("audio/cfg/diagnosis", cfg.diagnosis)
        self.router.register_prefix("audio/cfg/restart", cfg.restart)
        self.router.register_prefix("audio/cfg/devicestartup", cfg.trigger_startup)
        self.router.register_prefix("audio/cfg/trigger_startup", cfg.trigger_startup)
        self.router.register_prefix("audio/cfg/playername", cfg.player_name)
        self.router.register_prefix("audio/cfg/volumes", cfg.volumes)
        self.router.register_prefix("audio/cfg/getinputs", cfg.get_inputs)
        self.router.register_prefix("audio/cfg/all", cfg.get_all)

        self.router.register_prefix("audio/cfg/getservices", favs.get_services)
        self.router.register_prefix("audio/cfg/getavailableservices", favs.get_available_services)
        self.router.register_prefix("audio/cfg/getservicefolder", favs.get_service_folder)
        self.router.register_prefix("audio/cfg/globalsearch/describe", favs.global_search_describe)
        self.router.register_prefix("audio/cfg/globalsearch", favs.global_search)
        self.router.register_prefix("audio/cfg/getradios", favs.get_radios)
        self.router.register_prefix("audio/cfg/getplaylists2", favs.get_playlists)
        self.router.register_prefix("audio/cfg/getroomfavs", favs.get_room_favs)
        self.router.register_prefix("audio/cfg/roomfavs", favs.room_favs_action)

        # Register Regexes
        import re

        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/status/?$"), zone.get_status)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/recent"), zone.get_recent)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/geteq"), zone.get_eq)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/equalizer"), zone.equalizer)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/getqueue"), queue.get_queue)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/queue/clear"), queue.queue_clear)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/queue/play/"), queue.queue_play)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/queueandplay/"), queue.queue_and_play)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/queueadd/"), queue.queue_add)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/serviceplay/"), favs.service_play)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/playurl/"), favs.play_url)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/roomfav/play/"), favs.room_fav_play)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/roomfav/plus/?$"), favs.room_fav_plus)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/tts/"), alerts.zone_tts)
        self.router.register_regex(re.compile(r"^audio/(?:cfg/)?\d+/(?:bell|alarm|firealarm|clock|buzzer)"), alerts.zone_alert)
        self.router.register_regex(re.compile(r"^audio/grouped/"), alerts.grouped_alert)
        self.router.register_regex(
            re.compile(r"^audio/(?:cfg/)?\d+/(?:on|off|play|pause|resume|position|volume|queueplus|queueminus|repeat|shuffle)"),
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
