"""Unit tests for native Music Assistant provider package."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent / 'providers'))

import loxone
from loxone import LoxonePlugin, setup


class TestNativeLoxoneProvider(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.mock_mass = MagicMock()
        self.mock_mass.players = MagicMock()
        mock_p1 = MagicMock()
        mock_p1.player_id = 'player_1'
        mock_p1.display_name = 'Living Room'
        mock_p1.volume_level = 40
        self.mock_mass.players.all = [mock_p1]
        self.mock_mass.subscribe = MagicMock()

        self.mock_manifest = MagicMock()
        self.mock_config = MagicMock()

    async def test_provider_setup_and_config(self):
        plugin = await setup(self.mock_mass, self.mock_manifest, self.mock_config)
        self.assertIsInstance(plugin, LoxonePlugin)

        entries = await plugin.get_config_entries()
        self.assertTrue(len(entries) >= 5)
        keys = [getattr(e, 'key', '') for e in entries]
        self.assertIn('server_name', keys)
        self.assertIn('app_http_port', keys)
        self.assertIn('ms_http_port', keys)
        self.assertIn('udp_discovery_port', keys)

    async def test_provider_lifecycle(self):
        plugin = LoxonePlugin(self.mock_mass, self.mock_manifest, self.mock_config)
        plugin.server.start = AsyncMock()
        plugin.server.stop = AsyncMock()
        plugin.discovery.start = AsyncMock()
        plugin.discovery.stop = AsyncMock()

        await plugin.loaded_in_mass()
        self.assertEqual(len(plugin.zone_to_player), 1)
        self.assertEqual(plugin.zone_to_player[1], 'player_1')
        self.assertEqual(plugin.zone_states[1].name, 'Living Room')
        self.assertEqual(plugin.zone_states[1].volume, 40)

        # Verify event subscription
        self.mock_mass.subscribe.assert_called_once()

        # Verify notifier is wired with router and clean_mac
        self.assertEqual(plugin.notifier.router, plugin.router)
        self.assertNotIn(":", plugin.server_config.clean_mac)
        hw_events = plugin.notifier._build_hw_events()
        self.assertEqual(len(hw_events), 7)
        self.assertEqual(hw_events[0]["client_id"], f"{plugin.server_config.clean_mac}#1")

        # Teardown
        await plugin.unload()
        plugin.server.stop.assert_awaited_once()
        plugin.discovery.stop.assert_awaited_once()


if __name__ == '__main__':
    unittest.main()
