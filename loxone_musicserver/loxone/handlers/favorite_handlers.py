"""Loxone room favorites, radios, playlists and service playback handlers."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Dict, List, Match, Optional
from urllib.parse import unquote

from ..codec import BASE_FAVORITE_ZONE, encode_loxone_id
from ..responses import CommandResult, build_empty_response, build_response

logger = logging.getLogger("loxone.handlers.favorites")


class FavoriteHandlers:
    """Handles room favorites, radio streams, playlists, and direct service playback."""

    def __init__(
        self,
        get_favorites: Callable[[int, int, int], Awaitable[Dict[str, Any]]],
        play_favorite: Callable[[int, str], Awaitable[None]],
        cycle_favorite: Callable[[int], Awaitable[None]],
        get_radios: Callable[[], Awaitable[List[Dict[str, Any]]]],
        get_playlists: Callable[[int, int], Awaitable[List[Dict[str, Any]]]],
        play_media: Callable[[int, str], Awaitable[None]],
    ) -> None:
        self.get_favorites = get_favorites
        self.play_favorite = play_favorite
        self.cycle_favorite = cycle_favorite
        self.get_radios = get_radios
        self.get_playlists = get_playlists
        self.play_media = play_media

    async def get_room_favs(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns room favorites for a zone."""
        parts = command.strip("/").split("/")
        zone_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        start = int(parts[4]) if len(parts) > 4 and parts[4].isdigit() else 0
        limit = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 50

        favs = await self.get_favorites(zone_id, start, limit)
        return build_response(command, "getroomfavs", [favs])

    async def room_favs_action(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Handles roomfavs actions like play, add, delete, setname."""
        parts = command.strip("/").split("/")
        zone_id = int(parts[3]) if len(parts) > 3 and parts[3].isdigit() else 1
        action = parts[4].lower() if len(parts) > 4 else "play"

        if action == "play":
            fav_id = parts[5] if len(parts) > 5 else "1"
            await self.play_favorite(zone_id, fav_id)
            return build_empty_response(command)
        elif action == "plus":
            await self.cycle_favorite(zone_id)
            return build_empty_response(command)

        return build_empty_response(command)

    async def room_fav_play(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Direct play favorite: audio/<zone>/roomfav/play/<id>."""
        parts = command.strip("/").split("/")
        idx = 2 if len(parts) > 1 and parts[1] == "cfg" else 1
        zone_id = int(parts[idx]) if len(parts) > idx and parts[idx].isdigit() else 1
        fav_id = parts[idx + 3] if len(parts) > idx + 3 else "1"

        await self.play_favorite(zone_id, fav_id)
        return build_empty_response(command)

    async def room_fav_plus(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Cycle next room favorite: audio/<zone>/roomfav/plus."""
        parts = command.strip("/").split("/")
        idx = 2 if len(parts) > 1 and parts[1] == "cfg" else 1
        zone_id = int(parts[idx]) if len(parts) > idx and parts[idx].isdigit() else 1

        await self.cycle_favorite(zone_id)
        return build_empty_response(command)

    async def get_radios(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns radio stations available in Music Assistant."""
        radios = await self.get_radios()
        return build_response(command, "getradios", radios)

    async def get_services(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns streaming services configured."""
        services = [
            {"id": "musicassistant", "name": "Music Assistant", "icon": "service_ma"},
            {"id": "spotify", "name": "Spotify", "icon": "service_spotify"},
            {"id": "tunein", "name": "Radio / TuneIn", "icon": "service_tunein"},
        ]
        return build_response(command, "getservices", services)

    async def get_available_services(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns available service catalog."""
        services = [
            {"id": "musicassistant", "name": "Music Assistant", "icon": "service_ma"},
            {"id": "spotify", "name": "Spotify", "icon": "service_spotify"},
            {"id": "tunein", "name": "Radio / TuneIn", "icon": "service_tunein"},
        ]
        return build_response(command, "getavailableservices", services)

    async def get_playlists(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns playlists."""
        playlists = await self.get_playlists(0, 50)
        return build_response(command, "getplaylists2", playlists)

    async def service_play(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Plays media from a streaming service or library path."""
        parts = command.strip("/").split("/")
        idx = 2 if len(parts) > 1 and parts[1] == "cfg" else 1
        zone_id = int(parts[idx]) if len(parts) > idx and parts[idx].isdigit() else 1
        media_path = "/".join(parts[idx + 2 :]) if len(parts) > idx + 2 else ""
        decoded_path = unquote(media_path)

        await self.play_media(zone_id, decoded_path)
        return build_empty_response(command)

    async def play_url(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Plays a raw audio URL."""
        parts = command.strip("/").split("/")
        idx = 2 if len(parts) > 1 and parts[1] == "cfg" else 1
        zone_id = int(parts[idx]) if len(parts) > idx and parts[idx].isdigit() else 1
        url = "/".join(parts[idx + 2 :]) if len(parts) > idx + 2 else ""
        decoded_url = unquote(url)

        await self.play_media(zone_id, decoded_url)
        return build_empty_response(command)
