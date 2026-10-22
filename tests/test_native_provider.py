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

    async def test_provider_service_routing(self):
        plugin = LoxonePlugin(self.mock_mass, self.mock_manifest, self.mock_config)
        plugin._register_routes()

        # 1. Available services
        res_avail = await plugin.router.dispatch("audio/cfg/getavailableservices")
        self.assertEqual(res_avail.name, "getavailableservices")
        self.assertIn("config", res_avail.payload[0])

        # 2. Configured services
        res_serv = await plugin.router.dispatch("audio/cfg/getservices")
        self.assertEqual(res_serv.name, "getservices")

        # 3. Service folder for radio
        res_radio_folder = await plugin.router.dispatch("audio/cfg/getservicefolder/loxoneradio/noUser/root/0/50")
        self.assertEqual(res_radio_folder.name, "getservicefolder")

        # 4. Service folder for spotify/mass_library
        res_mass_folder = await plugin.router.dispatch("audio/cfg/getservicefolder/spotify/mass_library/root/0/50")
        self.assertEqual(res_mass_folder.name, "getservicefolder")

        # 5. Zone recent and eq
        res_recent = await plugin.router.dispatch("audio/1/recent")
        self.assertEqual(res_recent.name, "recent")

        res_eq = await plugin.router.dispatch("audio/1/geteq")
        self.assertEqual(res_eq.name, "geteq")

    async def test_zone_mapping_priority(self):
        p1 = MagicMock(player_id="p1", display_name="Kitchen")
        p2 = MagicMock(player_id="p2", display_name="Living Room")
        p3 = MagicMock(player_id="p3", display_name="Bedroom")
        p4 = MagicMock(player_id="p4", display_name="Office")
        self.mock_mass.players.all = [p1, p2, p3, p4]

        config_values = {
            "zone_1_player": "p2",
            "zone_2_player": "disabled",
            "zone_3_player": "auto",
        }
        mock_cfg = MagicMock()
        mock_cfg.get_value.side_effect = lambda k: config_values.get(k, None)

        plugin = LoxonePlugin(self.mock_mass, self.mock_manifest, mock_cfg)
        plugin.server.start = AsyncMock()
        plugin.discovery.start = AsyncMock()
        await plugin.loaded_in_mass()

        # Zone 1 explicit -> p2 (Living Room)
        self.assertEqual(plugin.zone_to_player[1], "p2")
        # Zone 2 disabled -> None
        self.assertNotIn(2, plugin.zone_to_player)
        # Remaining sorted: Bedroom (p3), Kitchen (p1), Office (p4)
        # Zone 3 -> p3
        self.assertEqual(plugin.zone_to_player[3], "p3")
        # Zone 4 -> p1
        self.assertEqual(plugin.zone_to_player[4], "p1")
        # Zone 5 -> p4
        self.assertEqual(plugin.zone_to_player[5], "p4")

    async def test_radio_library_favorites_and_catalog(self):
        r1 = MagicMock()
        r1.item_id = "101"
        r1.name = "Radio 1"
        r1.uri = "library://radio/101"
        r1.favorite = False
        r1.image = None
        r1.metadata = None

        r2 = MagicMock()
        r2.item_id = "102"
        r2.name = "Radio SRF 3"
        r2.uri = "library://radio/102"
        r2.favorite = True
        r2.image = None
        r2.metadata = None

        self.mock_mass.music = MagicMock()
        self.mock_mass.music.radios.library_items = AsyncMock(return_value=[r1, r2])

        plugin = LoxonePlugin(self.mock_mass, self.mock_manifest, self.mock_config)
        plugin._register_routes()

        # Call get_service_folder for "custom" (Meine Sender)
        res_custom = await plugin.router.dispatch("audio/cfg/getservicefolder/custom/custom/custom/0/20")
        self.assertEqual(res_custom.name, "getservicefolder")
        folder = res_custom.payload[0]
        self.assertEqual(folder["name"], "Meine Sender")
        # Prioritizes favorite stations
        self.assertEqual(len(folder["items"]), 1)
        self.assertEqual(folder["items"][0]["name"], "Radio SRF 3")
        self.assertTrue(folder["items"][0]["cover"].endswith("srf3.png"))

        # Verify _radio_catalog contains Radio SRF 3
        self.assertIn("102", plugin._radio_catalog)
        self.assertIn("radio srf 3", plugin._radio_catalog)

    async def test_radio_playback_updates_zone_state(self):
        plugin = LoxonePlugin(self.mock_mass, self.mock_manifest, self.mock_config)
        plugin.server.start = AsyncMock()
        plugin.discovery.start = AsyncMock()
        await plugin.loaded_in_mass()

        self.mock_mass.player_queues = MagicMock()
        self.mock_mass.player_queues.play_media = AsyncMock()

        # Seed radio catalog
        plugin._radio_catalog["http://stream.srg-ssr.ch/m/srf3/mp3_128"] = {
            "name": "Radio SRF 3",
            "station": "Radio SRF 3",
            "cover": "https://cdn.example.com/srf3.png",
        }

        # Play media on zone 1 (mapped to player_1)
        await plugin.router.dispatch("audio/1/serviceplay/custom/custom/http://stream.srg-ssr.ch/m/srf3/mp3_128/noshuffle")

        # Verify play_media was called on mass.player_queues
        self.mock_mass.player_queues.play_media.assert_awaited_once()

        # Verify zone 1 state updated with station info
        zstate = plugin.zone_states[1]
        self.assertEqual(zstate.power, "on")
        self.assertEqual(zstate.mode, "play")
        self.assertEqual(zstate.station, "Radio SRF 3")
        self.assertEqual(zstate.title, "Radio SRF 3")
        self.assertEqual(zstate.artist, "Radio")
        self.assertEqual(zstate.coverurl, "https://cdn.example.com/srf3.png")
        self.assertEqual(zstate.audiotype, 2)
        self.assertEqual(zstate.type, 2)


if __name__ == '__main__':
    unittest.main()
