# Loxone Music Server / AudioServer for Music Assistant

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Music Assistant](https://img.shields.io/badge/Music%20Assistant-2.x-brightgreen.svg)](https://music-assistant.io/)
[![Loxone Compatible](https://img.shields.io/badge/Loxone-Audioserver%20v17-orange.svg)](https://www.loxone.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A full-featured **Loxone Music Server / AudioServer emulation** and bridge for [Music Assistant](https://music-assistant.io/).

It emulates the native network and WebSocket protocols of a Loxone AudioServer / Music Server, allowing seamless integration into **Loxone Config**, the **Loxone Miniserver**, and the official **Loxone App** (iOS / Android / Web).

---

## Features

- **Native Loxone Discovery:**
  - UDP 7070 Broadcast Listener: Auto-detected in Loxone Config under *"Audio Server suchen"*.
  - mDNS / Bonjour Service: Advertises `_http._tcp.local.` as `Audioserver` with official TXT records.
- **Dual Protocol Endpoints:**
  - Port `7091` (`appHttp`): For Loxone Config and the Loxone App.
  - Port `7095` (`msHttp`): For the Loxone Miniserver.
  - Supports both plain HTTP and TLS on the same ports with automatic protocol detection.
- **WebSocket Real-time Events:**
  - Immediate greeting handshake: `LWSS V 17.1.05.05 ...`
  - Live zone state push: `audio_event` on track change, playback progress, volume adjustment, etc.
  - Queue change events: `audio_queue_event`.
  - Room favorites events: `roomfavchanged_event`.
  - Server heartbeat every 10 seconds: `server_heartbeat`.
- **Full Zone Playback & Controls:**
  - Play, Pause, Resume, Stop, Power On/Off.
  - Absolute & relative volume control (`0-100%`, `+3`, `-3`).
  - Seek / position scrubbing.
  - Next / previous track (`queueplus` / `queueminus`).
  - Repeat & Shuffle modes.
  - 10-band Equalizer settings.
- **Media & Sources:**
  - Direct integration with Music Assistant's library, radio stations, and streaming services (Spotify, TuneIn, etc.).
  - Room favorites cycling via Loxone Touch ("+" button).
  - Queue management (get, clear, insert, play index).
- **TTS & Smart Home Alerts:**
  - Zone TTS announcements (`audio/<zone>/tts/<text>/<volume>`).
  - Grouped TTS and sound events.
  - Doorbell / gong (`bell`), clock chime (`clock`), alarm (`alarm`), and fire alarm (`firealarm`).
- **Flexible Deployment:**
  - **Standalone Bridge / Docker Daemon:** Runs independently on your network, connecting to Music Assistant via WebSocket RPC.
  - **Native Music Assistant Plugin:** Contains standard `manifest.json` and `PluginProvider` code for running directly inside Music Assistant.

---

## Architecture

```
┌────────────────────────────────┐         ┌────────────────────────────────┐
│   Loxone Miniserver / Config   │         │      Loxone App (iOS/Android)  │
└──────────────┬─────────────────┘         └──────────────┬─────────────────┘
               │                                          │
       Port 7070 (UDP Discovery)                  Port 7091 (HTTP & WS)
       Port 7095 (HTTP & WS)                              │
               │                                          │
               ▼                                          ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                    Loxone Music Server Emulator                           │
│   - UDP 7070 Responder & mDNS Publisher                                   │
│   - Dual HTTP/TLS 7091 & 7095 Servers                                     │
│   - WebSocket Notifier & Heartbeat                                        │
│   - RSA Crypto & Config Handlers                                          │
│   - Zone State Projector (audio_event wire format)                        │
└─────────────────────────────────────┬─────────────────────────────────────┘
                                      │
                         WebSocket RPC (auth/login)
                                      ▼
┌───────────────────────────────────────────────────────────────────────────┐
│                          Music Assistant 2.x                              │
│   - Sonos / AirPlay / Chromecast / Squeezelite / Sendspin Players        │
│   - Unified Queue & Playback Engines                                      │
│   - Radio, Streaming Services & Library                                  │
└───────────────────────────────────────────────────────────────────────────┘
```

---

## Quick Start (Standalone Bridge)

### Option 1: Python

```bash
# Clone the repository
git clone https://github.com/TheRudin/LoxoneMusicServerMusicAssistant.git
cd LoxoneMusicServerMusicAssistant

# Install dependencies
pip install -r requirements.txt

# Start the emulator
python3 -m loxone_musicserver.main \
  --ma-url http://<music-assistant-ip>:8095 \
  --username admin \
  --password "your_password"
```

### Option 2: Docker Compose

```bash
docker compose up -d
```

---

## Configuration Options

Command line arguments and environment variables:

| CLI Option | Environment Variable | Default | Description |
|---|---|---|---|
| `--ma-url` | `MA_URL` | `http://localhost:8095` | URL to Music Assistant Server |
| `--username` | `MA_USERNAME` | `admin` | Music Assistant username |
| `--password` | `MA_PASSWORD` | `""` | Music Assistant password |
| `--token` | `MA_TOKEN` | `""` | Long-lived access token (optional) |
| `--host` | `LOXONE_HOST` | `0.0.0.0` | Host IP to bind to |
| `--app-port` | `LOXONE_APP_PORT` | `7091` | Loxone App HTTP/WS port |
| `--ms-port` | `LOXONE_MS_PORT` | `7095` | Miniserver HTTP/WS port |
| `--discovery-port` | `LOXONE_DISCOVERY_PORT` | `7070` | UDP Discovery port |
| `--mac` | `LOXONE_MAC` | *(auto)* | MAC address to announce to Loxone |
| `--data-dir` | `LOXONE_DATA_DIR` | `./data` | Directory for persistent cache |

---

## Loxone Config Integration

For detailed instructions on discovering and adding the AudioServer in **Loxone Config**, configuring audio zones, and using the **Loxone App**, see:

👉 **[LOXONE_SETUP.md](LOXONE_SETUP.md)**

---

## Running as Native Music Assistant Plugin

The plugin is also available as a fully self-contained Music Assistant Plugin Provider in `providers/loxone/`:

1. **Copy to Music Assistant:**
   Place the `providers/loxone` folder into your Music Assistant `providers` directory:
   ```bash
   # On Unraid Docker:
   docker cp providers/loxone music-assistant:/app/venv/lib/python3.12/site-packages/music_assistant/providers/loxone
   docker restart music-assistant
   ```
2. **Enable in Music Assistant UI:**
   Navigate to **Settings -> Plugins -> Add Plugin** -> Select **Loxone Music Server** and click **Save**.
3. Music Assistant will run the Loxone AudioServer in-process, listening on UDP 7070, TCP 7091, and TCP 7095 with zero network overhead.

---

## Testing & Endpoint Verification

Run the comprehensive 25-endpoint live verification suite:

```bash
python3 verify_endpoints.py
```

Run unit tests:

```bash
python3 -m unittest discover -s tests -p "test_*.py" -v
```

---

## Credits & Reference

- Inspired by and protocol-compatible with [sonn-audio/core](https://github.com/sonn-audio/core).
- Built for [Music Assistant](https://github.com/music-assistant/server).

---

## License

This project is licensed under the [MIT License](LICENSE).
