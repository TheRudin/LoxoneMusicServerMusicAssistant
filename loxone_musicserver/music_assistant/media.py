"""Media conversion helpers for Loxone catalog."""

from __future__ import annotations

from typing import Any, Dict, List


def format_radio_item(radio: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Formats a Music Assistant radio item for Loxone."""
    return {
        "id": index + 1,
        "name": radio.get("name", "Radio Station"),
        "coverurl": radio.get("image_url", ""),
        "source": radio.get("uri", ""),
        "type": 1,  # AudioType.Radio
    }


def format_playlist_item(playlist: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Formats a Music Assistant playlist item for Loxone."""
    return {
        "id": index + 1,
        "name": playlist.get("name", "Playlist"),
        "coverurl": playlist.get("image_url", ""),
        "source": playlist.get("uri", ""),
        "type": 3,  # FileType.Playlist
    }
