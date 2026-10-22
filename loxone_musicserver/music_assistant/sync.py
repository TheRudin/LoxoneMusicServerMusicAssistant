"""Bi-directional state synchronization between Music Assistant and Loxone zones."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .client import MusicAssistantClient
from ..loxone.codec import encode_loxone_id, BASE_FAVORITE_ZONE
from ..config import ServerConfig
from ..loxone.websocket import LoxoneWebSocketNotifier, LoxoneZoneState

logger = logging.getLogger("music_assistant.sync")


class MusicAssistantZoneSync:
    """Synchronizes Music Assistant players and queues with Loxone AudioServer zones."""

    def __init__(
        self,
        config: ServerConfig,
        ma_client: MusicAssistantClient,
        notifier: LoxoneWebSocketNotifier,
    ) -> None:
        self.config = config
        self.ma_client = ma_client
        self.notifier = notifier

        # Mapping: zone_id (int) <-> player_id (str)
        self.zone_to_player: Dict[int, str] = {}
        self.player_to_zone: Dict[str, int] = {}

        # Cached LoxoneZoneStates keyed by zone_id
        self.zone_states: Dict[int, LoxoneZoneState] = {}
        # Cached MA raw player and queue objects
        self.ma_players: Dict[str, Dict[str, Any]] = {}
        self.ma_queues: Dict[str, Dict[str, Any]] = {}

        # Register event callback
        self.ma_client.register_event_handler(self._on_ma_event)

    async def initialize(self) -> None:
        """Initial bootstrap: fetches all players and player queues from MA."""
        try:
            players = await self.ma_client.get_players()
            for p in players:
                pid = p.get("player_id")
                if pid:
                    self.ma_players[pid] = p

            queues = await self.ma_client.get_player_queues()
            for q in queues:
                qid = q.get("queue_id")
                if qid:
                    self.ma_queues[qid] = q

            self._build_default_zone_mapping()
            self._update_all_zone_states()
            logger.info("Initialized zone mapping: %d zones mapped to MA players", len(self.zone_to_player))
        except Exception as exc:
            logger.error("Failed to initialize zone mapping from MA: %s", exc)

    def _build_default_zone_mapping(self) -> None:
        """Assigns sequential zone IDs (1, 2, 3...) to discovered MA players."""
        zone_id = 1
        for pid, player in sorted(self.ma_players.items(), key=lambda item: item[1].get("name", "")):
            # Skip hidden or private players if desired
            if pid not in self.player_to_zone:
                self.zone_to_player[zone_id] = pid
                self.player_to_zone[pid] = zone_id
                logger.debug("Mapped Zone %d -> MA Player '%s' (%s)", zone_id, player.get("name"), pid)
                zone_id += 1

    def apply_loxone_config(self, raw_config: Any) -> None:
        """Reconciles zone mappings from Loxone Config setconfig payload."""
        if not isinstance(raw_config, list):
            return

        server_mac = self.config.mac_address.replace(":", "").replace("-", "").upper()
        players_config = []

        for item in raw_config:
            if isinstance(item, dict):
                for k, v in item.items():
                    if k.replace(":", "").replace("-", "").upper() == server_mac and isinstance(v, dict):
                        players_config = v.get("players", [])
                        break

        if not players_config:
            return

        logger.info("Applying %d zones from Loxone Config", len(players_config))
        # Clear existing mappings and assign configured ones
        self.zone_to_player.clear()
        self.player_to_zone.clear()

        # Match by name or ID
        for p_cfg in players_config:
            zid = int(p_cfg.get("id", 0))
            zname = str(p_cfg.get("name", "")).strip().lower()
            if not zid:
                continue

            # Try to match an MA player by name
            matched_pid = None
            for pid, player in self.ma_players.items():
                p_name = str(player.get("name", "")).strip().lower()
                if zname and (zname == p_name or zname in p_name or p_name in zname):
                    matched_pid = pid
                    break

            # Fallback: match by index
            if not matched_pid:
                keys = list(self.ma_players.keys())
                if zid - 1 < len(keys):
                    matched_pid = keys[zid - 1]

            if matched_pid:
                self.zone_to_player[zid] = matched_pid
                self.player_to_zone[matched_pid] = zid
                logger.info("Loxone Zone %d ('%s') bound to MA player '%s'", zid, p_cfg.get("name"), matched_pid)

        self._update_all_zone_states()

    def get_all_states(self) -> List[LoxoneZoneState]:
        """Returns current states of all mapped zones."""
        return list(self.zone_states.values())

    def get_zone_state(self, zone_id: int) -> Optional[LoxoneZoneState]:
        """Returns the state of a specific zone."""
        return self.zone_states.get(zone_id)

    def _update_all_zone_states(self) -> None:
        for zid, pid in self.zone_to_player.items():
            state = self._project_zone_state(zid, pid)
            self.zone_states[zid] = state

    def _project_zone_state(self, zone_id: int, player_id: str) -> LoxoneZoneState:
        player = self.ma_players.get(player_id, {})
        queue = self.ma_queues.get(player_id, {})
        cur_item = queue.get("current_item") or {}
        media_item = cur_item.get("media_item") or {}

        # Mode: "play", "pause", "stop"
        playback_state = str(player.get("playback_state", "idle")).lower()
        if playback_state == "playing":
            mode = "play"
        elif playback_state == "paused":
            mode = "pause"
        else:
            mode = "stop"

        # Power: "on", "off"
        powered = player.get("powered", True)
        power_str = "on" if powered else "off"

        # Volume: 0-100
        vol = player.get("volume_level")
        volume = int(vol) if vol is not None else 30

        # Current media metadata
        title = media_item.get("name") or cur_item.get("name") or ""
        artist = ""
        artists = media_item.get("artists", [])
        if artists and isinstance(artists, list):
            artist = artists[0].get("name", "")
        album = ""
        alb = media_item.get("album")
        if alb and isinstance(alb, dict):
            album = alb.get("name", "")

        duration = int(cur_item.get("duration") or media_item.get("duration") or 0)
        elapsed = int(queue.get("elapsed_time") or player.get("elapsed_time") or 0)

        # Image cover URL
        image_url = media_item.get("image_url") or cur_item.get("image_url") or ""
        if image_url and not image_url.startswith("http"):
            image_url = f"{self.config.ma_url.rstrip('/')}/{image_url.lstrip('/')}"

        # Repeat & shuffle
        repeat_mode = queue.get("repeat_mode", "off")
        plrepeat = 3 if repeat_mode == "one" else (1 if repeat_mode == "all" else 0)
        plshuffle = 1 if queue.get("shuffle_enabled") else 0

        # Audiotype: 0=File, 1=Radio, 2=Playlist, 4=AirPlay, 5=Spotify
        uri = cur_item.get("uri") or media_item.get("uri") or ""
        audiotype = 0
        if "radio" in uri or uri.startswith("http"):
            audiotype = 1
        elif "spotify" in uri:
            audiotype = 5
        elif "playlist" in uri:
            audiotype = 2

        zone_name = player.get("display_name") or player.get("name") or f"Zone {zone_id}"

        return LoxoneZoneState(
            playerid=zone_id,
            name=zone_name,
            power=power_str,
            mode=mode,
            clientState=power_str,
            time=elapsed,
            duration=duration,
            volume=volume,
            title=title,
            artist=artist,
            album=album,
            coverurl=image_url,
            audiopath=uri,
            audiotype=audiotype,
            plrepeat=plrepeat,
            plshuffle=plshuffle,
            qindex=int(queue.get("current_index") or 0),
        )

    def _on_ma_event(self, event: Dict[str, Any]) -> None:
        """Handles real-time events coming from Music Assistant."""
        evt_type = str(event.get("event", "")).lower()
        obj_id = str(event.get("object_id", ""))
        data = event.get("data")

        if evt_type in ("player_updated", "player_added") and isinstance(data, dict):
            pid = data.get("player_id") or obj_id
            self.ma_players[pid] = data
            zid = self.player_to_zone.get(pid)
            if zid:
                state = self._project_zone_state(zid, pid)
                self.zone_states[zid] = state
                self.notifier.notify_zone_state_changed(state)

        elif evt_type in ("queue_updated", "queue_items_updated") and isinstance(data, dict):
            qid = data.get("queue_id") or obj_id
            self.ma_queues[qid] = data
            zid = self.player_to_zone.get(qid)
            if zid:
                state = self._project_zone_state(zid, qid)
                self.zone_states[zid] = state
                self.notifier.notify_zone_state_changed(state)
                items_cnt = int(data.get("items") or 0)
                self.notifier.notify_queue_updated(zid, items_cnt)

    # --- Actions invoked by Loxone Handlers ---

    async def handle_player_command(self, zone_id: int, action: str, param: Any) -> None:
        """Sends playback commands to the mapped MA player."""
        player_id = self.zone_to_player.get(zone_id)
        if not player_id:
            logger.warning("Command %s on unmapped Zone %d", action, zone_id)
            return

        if action == "play":
            await self.ma_client.player_cmd(player_id, "play")
        elif action == "pause":
            await self.ma_client.player_cmd(player_id, "pause")
        elif action == "stop":
            await self.ma_client.player_cmd(player_id, "stop")
        elif action == "power_on":
            await self.ma_client.player_cmd(player_id, "power", {"powered": True})
        elif action == "volume":
            await self.ma_client.player_cmd(player_id, "volume_set", {"volume_level": int(param)})
        elif action == "seek":
            await self.ma_client.player_cmd(player_id, "seek", {"position": int(param)})
        elif action == "next":
            await self.ma_client.player_cmd(player_id, "next")
        elif action == "previous":
            await self.ma_client.player_cmd(player_id, "previous")
        elif action == "repeat":
            mode_str = "one" if param == 3 else ("all" if param == 1 else "off")
            await self.ma_client.queue_cmd(player_id, "repeat", {"repeat_mode": mode_str})
        elif action == "shuffle":
            await self.ma_client.queue_cmd(player_id, "shuffle", {"shuffle_enabled": bool(param)})

    async def handle_play_media(self, zone_id: int, media_path: str) -> None:
        """Plays media item in player queue."""
        player_id = self.zone_to_player.get(zone_id)
        if not player_id:
            return
        await self.ma_client.play_media(player_id, media_path)

    async def handle_queue_control(self, zone_id: int, cmd: str, param: Any) -> None:
        """Controls queue operations."""
        player_id = self.zone_to_player.get(zone_id)
        if not player_id:
            return

        if cmd == "clear":
            await self.ma_client.queue_cmd(player_id, "clear")
        elif cmd == "play_item":
            await self.ma_client.queue_cmd(player_id, "play_index", {"index": param})
        elif cmd == "play_media":
            await self.ma_client.play_media(player_id, str(param))
        elif cmd == "add":
            await self.ma_client.play_media(player_id, str(param), option="add")

    async def get_queue_data(self, zone_id: int, start: int, limit: int) -> Dict[str, Any]:
        """Fetches queue items for Loxone."""
        player_id = self.zone_to_player.get(zone_id)
        items: List[Dict[str, Any]] = []
        if player_id:
            raw_items = await self.ma_client.get_queue_items(player_id, limit=limit, offset=start)
            for idx, item in enumerate(raw_items):
                media = item.get("media_item") or {}
                items.append(
                    {
                        "id": str(item.get("queue_item_id") or idx),
                        "name": media.get("name") or item.get("name") or "Track",
                        "artist": (media.get("artists", [{}])[0].get("name") if media.get("artists") else ""),
                        "album": (media.get("album", {}).get("name") if isinstance(media.get("album"), dict) else ""),
                        "duration": int(item.get("duration") or 0),
                        "coverurl": media.get("image_url") or "",
                        "type": 2,
                    }
                )
        return {
            "id": f"queue_{zone_id}",
            "items": items,
            "shuffle": 0,
            "start": start,
            "totalitems": len(items),
        }

    async def get_favorites_data(self, zone_id: int, start: int, limit: int) -> Dict[str, Any]:
        """Provides room favorites list for Loxone."""
        # Surfaces radio stations and favorite playlists as room favorites
        radios = await self.ma_client.get_radios()
        items: List[Dict[str, Any]] = []
        for idx, r in enumerate(radios[:limit]):
            fav_id = idx + 1
            enc_id = encode_loxone_id(r.get("uri", ""), BASE_FAVORITE_ZONE + fav_id)
            items.append(
                {
                    "id": fav_id,
                    "name": r.get("name", f"Radio {fav_id}"),
                    "coverurl": r.get("image_url") or "",
                    "encId": enc_id,
                    "type": 1,  # Radio
                    "source": r.get("uri") or "",
                }
            )
        return {
            "totalitems": len(items),
            "start": start,
            "items": items,
        }

    async def play_favorite(self, zone_id: int, fav_id: str) -> None:
        """Plays favorite item."""
        radios = await self.ma_client.get_radios()
        try:
            idx = int(fav_id) - 1
            if 0 <= idx < len(radios):
                uri = radios[idx].get("uri")
                if uri:
                    await self.handle_play_media(zone_id, uri)
        except ValueError:
            pass

    async def cycle_favorite(self, zone_id: int) -> None:
        """Cycles to the next room favorite (called on plus button)."""
        radios = await self.ma_client.get_radios()
        if not radios:
            return
        state = self.get_zone_state(zone_id)
        current_fav_idx = 0
        if state and state.audiopath:
            for idx, r in enumerate(radios):
                if r.get("uri") == state.audiopath:
                    current_fav_idx = (idx + 1) % len(radios)
                    break
        uri = radios[current_fav_idx].get("uri")
        if uri:
            await self.handle_play_media(zone_id, uri)

    async def play_tts(self, zone_ids: List[int], text: str, lang: Optional[str], volume: Optional[int]) -> None:
        """Plays a TTS announcement to specified zones."""
        for zid in zone_ids:
            pid = self.zone_to_player.get(zid)
            if not pid:
                continue
            # Music Assistant announcement or TTS stream
            tts_uri = f"tts://{lang + '/' if lang else ''}{text}"
            try:
                await self.ma_client.play_media(pid, tts_uri)
            except Exception as exc:
                logger.warning("TTS playback error for zone %d: %s", zid, exc)

    async def play_alert(self, zone_ids: List[int], alert_type: str, volume: Optional[int]) -> None:
        """Plays sound effect / alarm to specified zones."""
        for zid in zone_ids:
            pid = self.zone_to_player.get(zid)
            if not pid:
                continue
            # Trigger sound effect announcement
            logger.info("Triggering alert '%s' on zone %d", alert_type, zid)
