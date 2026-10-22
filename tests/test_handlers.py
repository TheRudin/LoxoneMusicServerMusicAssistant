"""Integration tests for Loxone handlers."""

import asyncio
import unittest
from loxone_musicserver.config import ServerConfig
from loxone_musicserver.loxone.crypto import LoxoneCrypto
from loxone_musicserver.loxone.handlers.config_handlers import ConfigHandlers
from loxone_musicserver.loxone.handlers.favorite_handlers import FavoriteHandlers
from loxone_musicserver.loxone.handlers.secure_handlers import SecureHandlers
from loxone_musicserver.loxone.handlers.zone_handlers import ZoneHandlers
from loxone_musicserver.loxone.responses import serialize_result
from loxone_musicserver.loxone.websocket import LoxoneZoneState


class TestHandlers(unittest.TestCase):
    def setUp(self):
        self.config = ServerConfig()
        self.crypto = LoxoneCrypto()
        self.secure = SecureHandlers("TEST_SERIAL_123")
        self.cfg = ConfigHandlers(self.config, self.crypto)

    def test_secure_endpoints(self):
        async def run():
            res_hello = await self.secure.hello("secure/hello/test_pub_key")
            self.assertEqual(res_hello.name, "secure_hello")
            self.assertEqual(res_hello.payload["public_key"], "test_pub_key")
            self.assertTrue(res_hello.raw)

            # Test timestamp prefix and base64 with slashes
            res_hello_ts = await self.secure.hello("secure/hello/1792675208/LS0tLS1CRUdJTi/BDRVJUSUZJQ0FURS...")
            self.assertEqual(res_hello_ts.payload["public_key"], "LS0tLS1CRUdJTi/BDRVJUSUZJQ0FURS...")
            self.assertEqual(res_hello_ts.payload["error"], 0)
            self.assertTrue(res_hello_ts.raw)

            res_info = await self.secure.info_pairing("secure/info/pairing")
            self.assertEqual(res_info.payload["master"], "TEST_SERIAL_123")
            self.assertEqual(res_info.payload["error"], 0)
            self.assertTrue(res_info.raw)

            # Verify un-paired returns error -81
            sec_unpaired = SecureHandlers("")
            res_unpaired = await sec_unpaired.info_pairing("secure/info/pairing")
            self.assertEqual(res_unpaired.payload["error"], -81)
            self.assertEqual(res_unpaired.payload["master"], "")

            # Verify colons and hyphens in master serial are stripped
            sec_colons = SecureHandlers("50:4F:94:D0:1A:6D")
            self.assertEqual(sec_colons.clean_master, "504F94D01A6D")
            info_colons = await sec_colons.info_pairing("secure/info/pairing")
            self.assertEqual(info_colons.payload["master"], "504F94D01A6D")
            self.assertEqual(info_colons.payload["error"], 0)

            # Verify SecureHandlers linked with ServerConfig dynamically syncs
            cfg_dyn = ServerConfig(master_serial="50:4F:94:D0:1A:6D")
            sec_linked = SecureHandlers(config=cfg_dyn)
            self.assertEqual(sec_linked.clean_master, "504F94D01A6D")
            cfg_dyn.master_serial = "MS1A6D"
            self.assertEqual(sec_linked.clean_master, "MS1A6D")

            res_auth = await self.secure.authenticate("secure/authenticate/admin/token123")
            self.assertEqual(res_auth.payload, "authentication successful")
            self.assertFalse(res_auth.raw)
            self.assertIn('"authenticate_result": "authentication successful"', serialize_result(res_auth))

            res_init = await self.secure.init("secure/init")
            self.assertEqual(res_init.name, "secure_init")
            self.assertIn("jwt", res_init.payload)
            self.assertTrue(res_init.raw)

        asyncio.run(run())

    def test_default_mac(self):
        cfg = ServerConfig()
        self.assertEqual(cfg.mac_address, "79:F8:74:AD:39:43")
        self.assertEqual(cfg.clean_mac, "79F874AD3943")
        self.assertEqual(cfg.formatted_mac, "79:F8:74:AD:39:43")

    def test_config_endpoints(self):
        async def run():
            res_key = await self.cfg.get_key("audio/cfg/getkey")
            self.assertEqual(res_key.name, "getkey")
            self.assertEqual(res_key.payload[0]["exp"], 65537)

            res_ready = await self.cfg.ready("audio/cfg/ready")
            self.assertIn("session", res_ready.payload)

            res_ident = await self.cfg.identify("audio/cfg/identify")
            self.assertIn("mac", res_ident.payload)
            self.assertEqual(res_ident.payload["mac"], self.config.clean_mac)
            self.assertNotIn(":", res_ident.payload["mac"])

            ident_ms = self.config.get_identification("msHttp")
            self.assertIn(f"MINISERVER V {self.config.firmware_version} {self.config.clean_mac}", ident_ms)
            self.assertNotIn(f"{self.config.formatted_mac}", ident_ms)

        asyncio.run(run())

    def test_zone_volume_parsing(self):
        called = {}

        async def send_cmd(zid, action, param):
            called["zid"] = zid
            called["action"] = action
            called["param"] = param

        state = LoxoneZoneState(playerid=1, name="Kitchen", volume=40)
        zh = ZoneHandlers(lambda zid: state if zid == 1 else None, send_cmd)

        async def run():
            # Relative increase +5
            await zh.dynamic_command("audio/1/volume/+5")
            self.assertEqual(called["param"], 45)

            # Relative decrease -10
            await zh.dynamic_command("audio/1/volume/-10")
            self.assertEqual(called["param"], 30)

            # Absolute set 65
            await zh.dynamic_command("audio/1/volume/65")
            self.assertEqual(called["param"], 65)

            # Next track
            res_next = await zh.dynamic_command("audio/1/queueplus")
            self.assertEqual(res_next.name, "queueplus")
            self.assertEqual(called["action"], "next")

        asyncio.run(run())

    def test_config_extraction_and_devicestartup(self):
        import base64
        import json

        async def run():
            # Create sample Loxone setconfig JSON with matching clean_mac
            clean_mac = self.config.clean_mac
            sample_config = [
                {
                    clean_mac: {
                        "uuid": "14f3b890-023a-11ee-be56-0242ac120002",
                        "name": "Audioserver 101",
                        "ip": "192.168.1.107",
                        "port": 80,
                        "host": "192.168.1.10",
                        "master": "504F94D01A6D",
                        "players": [],
                        "extensions": [],
                    }
                }
            ]
            raw_json = json.dumps(sample_config)
            b64_payload = base64.b64encode(raw_json.encode("utf-8")).decode("utf-8")

            res = await self.cfg.set_config(f"audio/cfg/setconfig/{b64_payload}")
            self.assertEqual(res.name, "setconfig")
            self.assertIsNotNone(res.payload["crc32"])

            # Verify ServerConfig was updated
            self.assertEqual(self.config.uuid, "14f3b890-023a-11ee-be56-0242ac120002")
            self.assertEqual(self.config.miniserver_ip, "192.168.1.107")
            self.assertEqual(self.config.master_serial, "504F94D01A6D")
            self.assertEqual(self.config.server_name, "Audioserver 101")

            # Verify diagnosis returns enriched info
            diag = await self.cfg.diagnosis("audio/cfg/diagnosis")
            self.assertEqual(diag.payload["uuid"], "14f3b890-023a-11ee-be56-0242ac120002")
            self.assertEqual(diag.payload["miniserver_ip"], "192.168.1.107")
            self.assertEqual(diag.payload["master_serial"], "504F94D01A6D")
            self.assertTrue(diag.payload["paired"])

            # Test trigger_startup returns status dictionary
            startup_res = await self.cfg.trigger_startup("audio/cfg/trigger_startup")
            self.assertIn("timestamp", startup_res.payload)
            self.assertEqual(startup_res.payload["url"], "http://192.168.1.107:80/dev/sps/devicestartup/14f3b890-023a-11ee-be56-0242ac120002")

            # Test player_name handler
            updated_zones = {}
            self.cfg.on_player_name_updated = lambda zid, name: updated_zones.update({zid: name})
            pn_json = json.dumps([{"playerid": 1, "name": "Wohnzimmer"}, {"playerid": 2, "name": "Küche"}])
            pn_b64 = base64.b64encode(pn_json.encode("utf-8")).decode("utf-8")
            pn_res = await self.cfg.player_name(f"audio/cfg/playername/{pn_b64}")
            self.assertEqual(pn_res.name, "playername")
            self.assertEqual(updated_zones, {1: "Wohnzimmer", 2: "Küche"})

            # Test volumes, get_inputs, and get_all handlers
            vol_res = await self.cfg.volumes("audio/cfg/volumes")
            self.assertTrue(vol_res.payload["success"])

            in_res = await self.cfg.get_inputs("audio/cfg/getinputs")
            self.assertEqual(in_res.payload, [])

            all_res = await self.cfg.get_all("audio/cfg/all")
            self.assertTrue(all_res.payload["success"])

        asyncio.run(run())

    def test_services_and_folders(self):
        played_items = []

        async def get_favs(zid, start, limit):
            return {"totalitems": 0, "start": start, "items": []}

        async def play_fav(zid, fid):
            pass

        async def cycle_fav(zid):
            pass

        async def get_radios():
            return [
                {"id": "1", "name": "SRF 3", "station": "SRF 3", "cover": "http://img/srf3.png", "audiopath": "library://radio/1"}
            ]

        async def get_playlists(limit=50, offset=0):
            return [
                {"id": "10", "name": "Best of Rock", "totalitems": 25, "cover": "http://img/rock.png", "audiopath": "library://playlist/10"}
            ]

        async def get_albums(limit=50, offset=0):
            return [
                {"id": "100", "name": "Abbey Road", "artist": "The Beatles", "cover": "http://img/abbey.png", "audiopath": "library://album/100"}
            ]

        async def get_artists(limit=50, offset=0):
            return [
                {"id": "200", "name": "Queen", "cover": "http://img/queen.png", "audiopath": "library://artist/200"}
            ]

        async def play_media(zid, media):
            played_items.append((zid, media))

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

        async def run():
            # 1. Verify get_available_services contains config array (prevents n.config error in Loxone App)
            res_avail = await favs.get_available_services("audio/cfg/getavailableservices")
            self.assertEqual(res_avail.name, "getavailableservices")
            self.assertTrue(len(res_avail.payload) > 0)
            self.assertIn("config", res_avail.payload[0])
            self.assertIsInstance(res_avail.payload[0]["config"], list)
            self.assertTrue(len(res_avail.payload[0]["config"]) > 0)

            # 2. Verify get_services announces Music Assistant library
            res_services = await favs.get_services("audio/cfg/getservices")
            self.assertEqual(res_services.name, "getservices")
            self.assertEqual(res_services.payload[0]["cmd"], "spotify")
            self.assertEqual(res_services.payload[0]["id"], "mass_library")
            self.assertEqual(res_services.payload[0]["user"], "Music Assistant")

            # 3. Verify get_radios returns radio service catalog (local, custom, etc.) conforming to GetRadioSchema
            res_radios = await favs.get_radios("audio/cfg/getradios")
            self.assertEqual(res_radios.name, "getradios")
            cmds = [r["cmd"] for r in res_radios.payload]
            self.assertIn("local", cmds)
            self.assertIn("custom", cmds)
            for r in res_radios.payload:
                self.assertIn("name", r)
                self.assertIn("icon", r)
                self.assertIn("cmd", r)
                self.assertIn("root", r)

            # 4. Verify get_service_folder for radio (local, custom, and legacy folders)
            res_radio_local = await favs.get_service_folder("audio/cfg/getservicefolder/local/local/local/0/20")
            self.assertEqual(res_radio_local.name, "getservicefolder")
            self.assertEqual(len(res_radio_local.payload), 1)
            folder_local = res_radio_local.payload[0]
            self.assertEqual(folder_local["name"], "Lokale Sender")
            self.assertEqual(len(folder_local["items"]), 1)
            self.assertEqual(folder_local["items"][0]["name"], "SRF 3")
            self.assertEqual(folder_local["items"][0]["audiopath"], "library://radio/1")
            self.assertEqual(folder_local["items"][0]["type"], 2)

            res_radio_custom = await favs.get_service_folder("audio/cfg/getservicefolder/custom/custom/custom/0/20")
            self.assertEqual(res_radio_custom.payload[0]["name"], "Meine Sender")
            self.assertEqual(len(res_radio_custom.payload[0]["items"]), 1)

            # 5. Verify get_service_folder for loxoneradio
            res_radio_folder = await favs.get_service_folder("audio/cfg/getservicefolder/loxoneradio/noUser/root/0/50")
            self.assertEqual(res_radio_folder.name, "getservicefolder")
            self.assertEqual(len(res_radio_folder.payload), 1)
            folder_data = res_radio_folder.payload[0]
            self.assertEqual(folder_data["name"], "Radio")
            self.assertEqual(len(folder_data["items"]), 1)
            self.assertEqual(folder_data["items"][0]["name"], "SRF 3")
            self.assertEqual(folder_data["items"][0]["audiopath"], "library://radio/1")
            self.assertEqual(folder_data["items"][0]["type"], 2)

            # 6. Verify get_service_folder for spotify root (7 elements matching MediaEnum.Spotify.TYPES)
            res_mass_root = await favs.get_service_folder("audio/cfg/getservicefolder/spotify/mass_library/root/0/50")
            root_items = res_mass_root.payload[0]["items"]
            self.assertEqual(len(root_items), 7)
            ids = [it["id"] for it in root_items]
            self.assertIn("0", ids)  # Highlights
            self.assertIn("1", ids)  # New Releases
            self.assertIn("2", ids)  # Radios
            self.assertIn("3", ids)  # Playlists
            self.assertIn("4", ids)  # Liked Songs
            self.assertIn("5", ids)  # Albums
            self.assertIn("6", ids)  # Artists
            for it in root_items:
                self.assertIn("tag", it)

            # 7. Verify get_service_folder sub-folders
            res_playlists = await favs.get_service_folder("audio/cfg/getservicefolder/spotify/mass_library/3/0/50")
            self.assertEqual(res_playlists.payload[0]["items"][0]["name"], "Best of Rock")
            self.assertEqual(res_playlists.payload[0]["items"][0]["audiopath"], "library://playlist/10")
            self.assertEqual(res_playlists.payload[0]["items"][0]["tag"], "playlist")

            res_albums = await favs.get_service_folder("audio/cfg/getservicefolder/spotify/mass_library/5/0/50")
            self.assertEqual(res_albums.payload[0]["items"][0]["name"], "Abbey Road")
            self.assertEqual(res_albums.payload[0]["items"][0]["audiopath"], "library://album/100")
            self.assertEqual(res_albums.payload[0]["items"][0]["tag"], "album")

            res_artists = await favs.get_service_folder("audio/cfg/getservicefolder/spotify/mass_library/6/0/50")
            self.assertEqual(res_artists.payload[0]["items"][0]["name"], "Queen")
            self.assertEqual(res_artists.payload[0]["items"][0]["audiopath"], "library://artist/200")
            self.assertEqual(res_artists.payload[0]["items"][0]["tag"], "artist")

            # 8. Verify radio fallback when get_radios returns empty list
            async def get_empty_radios():
                return []

            favs_empty_radios = FavoriteHandlers(
                get_favs,
                play_fav,
                cycle_fav,
                get_empty_radios,
                get_playlists,
                play_media,
            )
            res_fallback = await favs_empty_radios.get_service_folder("audio/cfg/getservicefolder/local/local/local/0/20")
            fallback_items = res_fallback.payload[0]["items"]
            self.assertGreater(len(fallback_items), 0)
            self.assertEqual(fallback_items[0]["name"], "Radio SRF 3")
            self.assertEqual(fallback_items[0]["type"], 2)

            # 9. Verify serviceplay handles loxoneradio and spotify URLs and returns ok
            res_sp1 = await favs.service_play("audio/1/serviceplay/loxoneradio/noUser/library%3A%2F%2Fradio%2F1")
            self.assertEqual(played_items[-1], (1, "library://radio/1"))
            self.assertEqual(res_sp1.payload, "ok")

            res_sp2 = await favs.service_play("audio/2/serviceplay/spotify/mass_library/library%3A%2F%2Fplaylist%2F10")
            self.assertEqual(played_items[-1], (2, "library://playlist/10"))
            self.assertEqual(res_sp2.payload, "ok")

            # 10. Global search endpoints
            gs_desc = await favs.global_search_describe("audio/cfg/globalsearch/describe")
            self.assertEqual(gs_desc.name, "globalsearch")
            self.assertIn("spotify", gs_desc.payload)

            gs = await favs.global_search("audio/cfg/globalsearch")
            self.assertEqual(gs.name, "globalsearch")
            self.assertIn("unique", gs.payload)

        asyncio.run(run())

    def test_zone_recent_and_eq(self):
        zh = ZoneHandlers(lambda zid: None, lambda zid, action, param: asyncio.sleep(0))

        async def run():
            res_recent = await zh.get_recent("audio/1/recent")
            self.assertEqual(res_recent.name, "recent")
            self.assertEqual(res_recent.payload, [])

            res_eq = await zh.get_eq("audio/1/geteq")
            self.assertEqual(res_eq.name, "geteq")
            self.assertEqual(res_eq.payload, [])

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
