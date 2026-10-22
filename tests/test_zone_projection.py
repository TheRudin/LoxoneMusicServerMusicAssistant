"""Unit tests for Loxone Zone State Projection."""

import unittest
from loxone_musicserver.loxone.websocket import LoxoneZoneState


class TestZoneProjection(unittest.TestCase):
    def test_zone_state_to_dict(self):
        state = LoxoneZoneState(
            playerid=1,
            name="Wohnzimmer",
            power="on",
            mode="play",
            volume=35,
            title="Song Title",
            artist="Artist Name",
            album="Album Name",
            duration=240,
            time=60,
        )
        d = state.to_dict()

        self.assertEqual(d["playerid"], 1)
        self.assertEqual(d["name"], "Wohnzimmer")
        self.assertEqual(d["power"], "on")
        self.assertEqual(d["mode"], "play")
        self.assertEqual(d["volume"], 35)
        self.assertEqual(d["title"], "Song Title")
        self.assertEqual(d["artist"], "Artist Name")
        self.assertEqual(d["album"], "Album Name")
        self.assertEqual(d["duration"], 240)
        self.assertEqual(d["time"], 60)
        self.assertEqual(d["equalizerSettings"], "0,0,0,0,0,0,0,0,0,0")

    def test_title_fallback_to_name(self):
        state = LoxoneZoneState(
            playerid=2,
            name="Badezimmer",
            title="",
        )
        d = state.to_dict()
        self.assertEqual(d["title"], "Badezimmer")


if __name__ == "__main__":
    unittest.main()
