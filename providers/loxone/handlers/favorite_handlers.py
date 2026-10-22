"""Loxone room favorites, radios, playlists and service playback handlers."""

from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable, Dict, List, Match, Optional
from urllib.parse import unquote

from ..codec import BASE_FAVORITE_ZONE, encode_loxone_id
from ..responses import CommandResult, build_empty_response, build_response

logger = logging.getLogger("loxone.handlers.favorites")

DEFAULT_RADIO_STATIONS: List[Dict[str, Any]] = [
    {
        "id": "srf3",
        "name": "Radio SRF 3",
        "station": "Radio SRF 3",
        "audiopath": "http://stream.srg-ssr.ch/m/srf3/mp3_128",
        "cover": "https://raw.githubusercontent.com/TheRudin/LoxoneMusicServerMusicAssistant/main/assets/srf3.png",
    },
    {
        "id": "swisspop",
        "name": "Radio Swiss Pop",
        "station": "Radio Swiss Pop",
        "audiopath": "http://stream.srg-ssr.ch/m/rsp/mp3_128",
        "cover": "https://raw.githubusercontent.com/TheRudin/LoxoneMusicServerMusicAssistant/main/assets/swisspop.png",
    },
    {
        "id": "srf1",
        "name": "Radio SRF 1",
        "station": "Radio SRF 1",
        "audiopath": "http://stream.srg-ssr.ch/m/srf1/mp3_128",
        "cover": "https://raw.githubusercontent.com/TheRudin/LoxoneMusicServerMusicAssistant/main/assets/srf1.png",
    },
    {
        "id": "energy_basel",
        "name": "Energy Basel",
        "station": "Energy Basel",
        "audiopath": "http://energybasel.ice.infomaniak.ch/energybasel-high.mp3",
        "cover": "",
    },
    {
        "id": "energy_zuerich",
        "name": "Energy Zürich",
        "station": "Energy Zürich",
        "audiopath": "http://energyzuerich.ice.infomaniak.ch/energyzuerich-high.mp3",
        "cover": "",
    },
    {
        "id": "1live",
        "name": "1LIVE",
        "station": "1LIVE",
        "audiopath": "http://wdr-1live-live.icecast.wdr.de/wdr/1live/live/mp3/128/stream.mp3",
        "cover": "",
    },
    {
        "id": "swr3",
        "name": "SWR3",
        "station": "SWR3",
        "audiopath": "http://liveradio.swr.de/sw282p3/swr3/mp3/128/stream.mp3",
        "cover": "",
    },
    {
        "id": "bayern3",
        "name": "Bayern 3",
        "station": "Bayern 3",
        "audiopath": "http://dispatcher.rndfnk.com/br/br3/live/mp3/mid",
        "cover": "",
    },
]


def extract_service_play_media(command: str) -> tuple[int, str, Optional[bool]]:
    """Extracts zone_id, clean media URI, and optional shuffle flag from a serviceplay command."""
    sp_idx = command.find("serviceplay/")
    if sp_idx == -1:
        return 1, "", None

    prefix = command[:sp_idx].strip("/")
    zparts = prefix.split("/")
    zone_id = 1
    for p in reversed(zparts):
        if p.isdigit():
            zone_id = int(p)
            break

    rest = command[sp_idx + len("serviceplay/") :]
    parts = rest.split("/", 2)
    raw_media = parts[2] if len(parts) >= 3 else parts[-1]
    raw_media = raw_media.split("?")[0].rstrip("/")

    shuffle: Optional[bool] = None
    if raw_media.endswith("/shuffle"):
        shuffle = True
        raw_media = raw_media[:-8]
    elif raw_media.endswith("/noshuffle"):
        shuffle = False
        raw_media = raw_media[:-10]

    raw_media = raw_media.rstrip("/")
    decoded = unquote(raw_media)
    if "://" in decoded:
        proto, path = decoded.split("://", 1)
        decoded = f"{proto}://{path.lstrip('/')}"
    elif ":/" in decoded:
        proto, path = decoded.split(":/", 1)
        decoded = f"{proto}://{path.lstrip('/')}"

    return zone_id, decoded, shuffle


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
        get_albums: Optional[Callable[[int, int], Awaitable[List[Dict[str, Any]]]]] = None,
        get_artists: Optional[Callable[[int, int], Awaitable[List[Dict[str, Any]]]]] = None,
        get_playlist_tracks: Optional[Callable[[str], Awaitable[List[Dict[str, Any]]]]] = None,
        get_album_tracks: Optional[Callable[[str], Awaitable[List[Dict[str, Any]]]]] = None,
        get_artist_tracks: Optional[Callable[[str], Awaitable[List[Dict[str, Any]]]]] = None,
    ) -> None:
        self.get_favorites = get_favorites
        self.play_favorite = play_favorite
        self.cycle_favorite = cycle_favorite
        self._get_radios = get_radios
        self._get_playlists = get_playlists
        self.play_media = play_media
        self._get_albums = get_albums
        self._get_artists = get_artists
        self._get_playlist_tracks = get_playlist_tracks
        self._get_album_tracks = get_album_tracks
        self._get_artist_tracks = get_artist_tracks

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
        """Returns radio service catalog conforming to Loxone GetRadioSchema."""
        services = [
            {
                "name": "Lokale Sender",
                "icon": "LocationPin",
                "cmd": "local",
                "root": "local",
                "id": "local",
                "user": "nouser",
                "configerror": False,
            },
            {
                "name": "Meine Sender",
                "icon": "MusicFolderSong",
                "cmd": "custom",
                "root": "custom",
                "id": "custom",
                "user": "nouser",
                "configerror": False,
            },
            {
                "name": "Musik",
                "icon": "TheaterMask",
                "cmd": "music",
                "root": "music",
                "id": "music",
                "user": "nouser",
                "configerror": False,
            },
            {
                "name": "Länder",
                "icon": "Globe3",
                "cmd": "world",
                "root": "world",
                "id": "world",
                "user": "nouser",
                "configerror": False,
            },
        ]
        return build_response(command, "getradios", services)

    async def get_services(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns configured streaming services."""
        services = [
            {
                "cmd": "spotify",
                "name": "",
                "icon": "https://raw.githubusercontent.com/music-assistant/frontend/main/src/assets/logo.png",
                "id": "mass_library",
                "user": "Music Assistant",
                "email": "musicassistant@local",
                "product": "premium",
                "asdefault": [],
                "offline_storage": [],
                "configerror": False,
                "provider": "musicassistant",
                "fake": True,
            }
        ]
        return build_response(command, "getservices", services)

    async def get_available_services(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns available service catalog with expected config schemas."""
        services = [
            {
                "cmd": "spotify",
                "name": "Spotify",
                "icon": "https://extended-app-content.s3.eu-central-1.amazonaws.com/audioZone/services/Icon-Spotify.svg",
                "helplink": "http://www.loxone.com/help/musicserver-spotify",
                "registerlink": "https://www.spotify.com/signup",
                "config": [
                    {
                        "name": "Username",
                        "regex": "%5B%5E%3C%3E%26%25%5C%5C%2F'%5D%7B3%2C99%7D%24",
                        "type": "text",
                    },
                    {
                        "link": "https://www.spotify.com/legal/end-user-agreement",
                        "name": "EULA",
                        "type": "eula",
                    },
                ],
            }
        ]
        return build_response(command, "getavailableservices", services)

    async def get_playlists(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns playlists."""
        raw_playlists = await self._get_playlists(50, 0)
        playlists = []
        for p in raw_playlists:
            playlists.append({
                "id": str(p.get("id") or p.get("item_id", len(playlists) + 1)),
                "name": p.get("name", "Playlist"),
                "totalitems": p.get("totalitems", len(raw_playlists)),
                "cover": p.get("cover", ""),
                "audiopath": p.get("audiopath", f"playlist:{p.get('id', '')}"),
                "tag": "playlist",
                "type": 7,
            })
        return build_response(command, "getplaylists2", playlists)

    async def get_service_folder(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Handles audio/cfg/getservicefolder/<service>/<user>/<path>/<offset>/<limit>."""
        cmd_clean = command.split("?")[0].strip("/")
        parts = cmd_clean.split("/")
        idx = 3 if len(parts) > 1 and parts[1] == "cfg" else 2
        service = parts[idx].lower() if len(parts) > idx else "loxoneradio"
        user = parts[idx + 1] if len(parts) > idx + 1 else "noUser"
        limit = int(parts[-1]) if len(parts) > 1 and parts[-1].isdigit() else 50
        offset = int(parts[-2]) if len(parts) > 2 and parts[-2].isdigit() else 0
        raw_path_parts = (
            parts[idx + 2 : -2]
            if len(parts) > idx + 4
            else (parts[idx + 2 : -1] if len(parts) > idx + 3 else ["root"])
        )
        raw_path = "/".join(raw_path_parts) if raw_path_parts else "root"
        folder_path = unquote(raw_path).strip("/")

        items: List[Dict[str, Any]] = []
        folder_name = "Radio"
        is_pre_paged = False

        fp_norm = folder_path.replace(":///", "://")
        album_id: Optional[str] = None
        playlist_id: Optional[str] = None
        artist_id: Optional[str] = None

        if fp_norm.startswith("library://album/"):
            album_id = fp_norm[len("library://album/"):].split("/")[0]
        elif fp_norm.startswith("album:"):
            album_id = fp_norm[len("album:"):].split("/")[0]
        elif fp_norm.startswith("album/"):
            album_id = fp_norm[len("album/"):].split("/")[0]
        elif fp_norm.startswith("library://playlist/"):
            playlist_id = fp_norm[len("library://playlist/"):].split("/")[0]
        elif fp_norm.startswith("playlist:"):
            playlist_id = fp_norm[len("playlist:"):].split("/")[0]
        elif fp_norm.startswith("playlist/"):
            playlist_id = fp_norm[len("playlist/"):].split("/")[0]
        elif fp_norm.startswith("library://artist/"):
            artist_id = fp_norm[len("library://artist/"):].split("/")[0]
        elif fp_norm.startswith("artist:"):
            artist_id = fp_norm[len("artist:"):].split("/")[0]
        elif fp_norm.startswith("artist/"):
            artist_id = fp_norm[len("artist/"):].split("/")[0]
        elif fp_norm.isdigit() and int(fp_norm) > 6:
            playlist_id = fp_norm

        if album_id and self._get_album_tracks:
            raw_tracks = await self._get_album_tracks(album_id)
            folder_name = raw_tracks[0].get("album") if raw_tracks and raw_tracks[0].get("album") else f"Album {album_id}"
            items.extend(raw_tracks)
        elif playlist_id and self._get_playlist_tracks:
            raw_tracks = await self._get_playlist_tracks(playlist_id)
            folder_name = f"Playlist {playlist_id}"
            items.extend(raw_tracks)
        elif artist_id and self._get_artist_tracks:
            raw_tracks = await self._get_artist_tracks(artist_id)
            folder_name = raw_tracks[0].get("artist") if raw_tracks and raw_tracks[0].get("artist") else f"Künstler {artist_id}"
            items.extend(raw_tracks)
        elif service in ("local", "custom", "music", "world", "loxoneradio", "radio", "tunein"):
            if folder_path == "local" or service == "local":
                folder_name = "Lokale Sender"
            elif folder_path == "custom" or service == "custom":
                folder_name = "Meine Sender"
            elif folder_path == "music" or service == "music":
                folder_name = "Musik"
            elif folder_path == "world" or service == "world":
                folder_name = "Länder"
            else:
                folder_name = "Radio"

            raw_radios = await self._get_radios()
            if not raw_radios:
                raw_radios = list(DEFAULT_RADIO_STATIONS)
                try:
                    pls = await self._get_playlists(50, 0)
                    for p in pls:
                        pname = p.get("name", "")
                        if "radio" in pname.lower():
                            raw_radios.append({
                                "id": str(p.get("id") or p.get("item_id")),
                                "name": pname,
                                "station": pname,
                                "audiopath": p.get("audiopath", f"playlist:{p.get('id', '')}"),
                                "cover": p.get("cover", ""),
                            })
                except Exception:
                    pass

            for r in raw_radios:
                items.append({
                    "id": str(r.get("id") or r.get("item_id", len(items) + 1)),
                    "name": r.get("name", "Radio"),
                    "station": r.get("station") or r.get("name", "Radio"),
                    "audiopath": r.get("audiopath", f"radio:{r.get('id', '')}"),
                    "coverurl": r.get("cover", ""),
                    "type": 2,
                })
        else:
            # Streaming Service: Spotify / Music Assistant
            if folder_path in ("root", "start", ""):
                folder_name = "Music Assistant"
                # Loxone expects 7 entries (0..6) matching MediaEnum.Spotify.TYPES
                items = [
                    {
                        "id": "0",
                        "name": "Highlights",
                        "tag": "category",
                        "type": 1,
                        "audiopath": "folder:playlists",
                    },
                    {
                        "id": "1",
                        "name": "Neuerscheinungen",
                        "tag": "category",
                        "type": 1,
                        "audiopath": "folder:playlists",
                    },
                    {
                        "id": "2",
                        "name": "Radiosender",
                        "tag": "category",
                        "type": 1,
                        "audiopath": "folder:radios",
                    },
                    {
                        "id": "3",
                        "name": "Playlists",
                        "tag": "playlist",
                        "type": 7,
                        "audiopath": "folder:playlists",
                        "coverurl": "",
                        "totalitems": 50,
                    },
                    {
                        "id": "4",
                        "name": "Lieblingssongs",
                        "tag": "user_collection",
                        "type": 7,
                        "audiopath": "folder:favorites",
                        "coverurl": "",
                    },
                    {
                        "id": "5",
                        "name": "Alben",
                        "tag": "album",
                        "type": 7,
                        "audiopath": "folder:albums",
                        "coverurl": "",
                    },
                    {
                        "id": "6",
                        "name": "Künstler",
                        "tag": "artist",
                        "type": 7,
                        "audiopath": "folder:artists",
                        "coverurl": "",
                    },
                ]
            elif folder_path in ("3", "playlists", "0", "1"):
                folder_name = "Playlists"
                playlists = await self._get_playlists(limit, offset)
                is_pre_paged = True
                for p in playlists:
                    items.append({
                        "id": str(p.get("id") or p.get("item_id", len(items) + 1)),
                        "name": p.get("name", "Playlist"),
                        "tag": "playlist",
                        "type": 7,
                        "audiopath": p.get("audiopath", f"playlist:{p.get('id', '')}"),
                        "coverurl": p.get("cover", ""),
                        "totalitems": p.get("totalitems", 0),
                    })
            elif folder_path in ("5", "albums") and self._get_albums:
                folder_name = "Alben"
                albums = await self._get_albums(limit, offset)
                is_pre_paged = True
                for a in albums:
                    items.append({
                        "id": str(a.get("id") or a.get("item_id", len(items) + 1)),
                        "name": a.get("name", "Album"),
                        "artist": a.get("artist", ""),
                        "tag": "album",
                        "type": 7,
                        "audiopath": a.get("audiopath", f"album:{a.get('id', '')}"),
                        "coverurl": a.get("cover", ""),
                    })
            elif folder_path in ("6", "artists") and self._get_artists:
                folder_name = "Künstler"
                artists = await self._get_artists(limit, offset)
                is_pre_paged = True
                for ar in artists:
                    items.append({
                        "id": str(ar.get("id") or ar.get("item_id", len(items) + 1)),
                        "name": ar.get("name", "Künstler"),
                        "tag": "artist",
                        "type": 7,
                        "audiopath": ar.get("audiopath", f"artist:{ar.get('id', '')}"),
                        "coverurl": ar.get("cover", ""),
                    })
            elif folder_path in ("2", "radios"):
                folder_name = "Radiosender"
                raw_radios = await self._get_radios()
                if not raw_radios:
                    raw_radios = list(DEFAULT_RADIO_STATIONS)
                for r in raw_radios:
                    items.append({
                        "id": str(r.get("id") or r.get("item_id", len(items) + 1)),
                        "name": r.get("name", "Radio"),
                        "station": r.get("station") or r.get("name", "Radio"),
                        "audiopath": r.get("audiopath", f"radio:{r.get('id', '')}"),
                        "coverurl": r.get("cover", ""),
                        "type": 2,
                    })
            elif folder_path in ("4", "favorites", "Lieblingssongs"):
                folder_name = "Lieblingssongs"
                if self._get_playlist_tracks:
                    raw_tracks = await self._get_playlist_tracks("favorites")
                    for t in raw_tracks:
                        items.append(t)

        for it in items:
            if it.get("thumbnail") is None:
                it["thumbnail"] = it.get("coverurl") or it.get("cover") or ""
            if it.get("coverurl") is None:
                it["coverurl"] = it.get("thumbnail") or it.get("cover") or ""

        if is_pre_paged:
            paged_items = items
            total_count = offset + len(items) + (1 if len(items) == limit else 0)
        else:
            paged_items = items[offset : offset + limit] if (offset > 0 or limit < len(items)) else items
            total_count = len(items)

        folder_payload = {
            "id": folder_path,
            "name": folder_name,
            "start": offset,
            "totalitems": total_count,
            "service": service,
            "items": paged_items,
        }
        return build_response(command, "getservicefolder", [folder_payload])

    async def global_search_describe(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns global search description."""
        desc = {
            "spotify": ["track", "album", "artist", "playlist"],
            "local": ["track", "album", "artist", "playlist"],
            "tunein": ["station", "custom"],
        }
        return build_response(command, "globalsearch", desc)

    async def global_search(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Returns search token."""
        unique = f"gs-{int(time.time() * 1000)}"
        return build_response(command, "globalsearch", {"unique": unique})

    async def service_play(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Plays media from a streaming service or library path."""
        zone_id, decoded_path, shuffle = extract_service_play_media(command)
        if not decoded_path:
            return build_response(command, "serviceplay", "ok")

        logger.info("service_play for zone %d: %s (shuffle=%s)", zone_id, decoded_path, shuffle)
        await self.play_media(zone_id, decoded_path)
        return build_response(command, "serviceplay", "ok")

    async def play_url(self, command: str, payload: Optional[bytes] = None, match: Optional[Match[str]] = None) -> CommandResult:
        """Plays a raw audio URL."""
        parts = command.strip("/").split("/")
        idx = 2 if len(parts) > 1 and parts[1] == "cfg" else 1
        zone_id = int(parts[idx]) if len(parts) > idx and parts[idx].isdigit() else 1
        url = "/".join(parts[idx + 2 :]) if len(parts) > idx + 2 else ""
        decoded_url = unquote(url)

        await self.play_media(zone_id, decoded_url)
        return build_empty_response(command)
