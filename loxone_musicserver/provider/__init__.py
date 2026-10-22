"""Music Assistant Provider Package for Loxone AudioServer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest
    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType


async def setup(
    mass: Any,
    manifest: Any,
    config: Any,
) -> Any:
    """Initialize provider instance with given configuration."""
    from .provider import LoxoneMusicServerPlugin

    return LoxoneMusicServerPlugin(mass, manifest, config)
