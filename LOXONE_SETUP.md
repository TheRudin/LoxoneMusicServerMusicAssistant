# Loxone Music Server / AudioServer Einbindung & Konfiguration

Diese Anleitung beschreibt Schritt für Schritt, wie Sie den mit **Music Assistant** verbundenen AudioServer in **Loxone Config** und der **Loxone App** einbinden und bedienen.

---

## 1. Voraussetzungen & Netzwerk-Ports

Der Emulator stellt die exakten Ports und Protokolle eines physischen Loxone AudioServers zur Verfügung:

| Port | Protokoll | Zweck |
|---|---|---|
| **7070** | UDP | AudioServer Discovery (wird von Loxone Config zum Suchen genutzt) |
| **7091** | TCP (HTTP/WS) | `appHttp`: Schnittstelle für Loxone App und Config |
| **7095** | TCP (HTTP/WS) | `msHttp`: Schnittstelle für den Loxone Miniserver |
| **5353** | UDP | mDNS / Bonjour Dienst (`_http._tcp.local.`, devicetype=Audioserver) |

> [!IMPORTANT]
> Stellen Sie sicher, dass Ihr Rechner oder Container im **selben Subnetz** (Layer 2) wie Ihr Miniserver und Ihr PC mit Loxone Config läuft (z. B. `192.168.1.0/24`), damit die UDP-Broadcasts auf Port 7070 ankommen. Bei Docker empfiehlt sich daher `network_mode: host`.

---

## 2. AudioServer in Loxone Config einbinden

1. Öffnen Sie Ihr Projekt in **Loxone Config**.
2. Klicken Sie im Peripheriebaum auf den Reiter **Audio** oder **Geräte suchen**.
3. Wählen Sie **Audio Server suchen** (oder Netzwerk-Suche).
4. Der Emulator meldet sich automatisch als:
   - **Gerätetyp:** `Audioserver`
   - **Name:** `MusicAssistant-AudioServer` (oder Ihr konfigurierter Name)
   - **IP-Adresse:** IP Ihres laufenden Emulators
   - **Port:** `7091`
   - **Firmware:** `17.1.05.05`
5. Wählen Sie das gefundene Gerät aus und klicken Sie auf **Gerät anlegen** bzw. **Hinzufügen**.

---

## 3. Audio-Zonen anlegen und verknüpfen

1. Nach dem Hinzufügen des AudioServers können Sie Audio-Zonen (Player) definieren:
   - **Zone 1:** z. B. `Wohnzimmer`
   - **Zone 2:** z. B. `Badezimmer`
   - Weitere Zonen nach Bedarf
2. Die Zonen werden automatisch mit den entsprechenden Playern in Music Assistant synchronisiert (z. B. Sonos, AirPlay, Sendspin, Chromecast).
3. Verknüpfen Sie die Audio-Zonen in Loxone Config mit den entsprechenden Räumen und Bausteinen (*Audio Player Baustein*).
4. Speichern Sie das Projekt in den Miniserver (**In Miniserver speichern**).
5. Beim Speichern überträgt Loxone Config die Konfiguration via `audio/cfg/setconfig` verschlüsselt an den Emulator. Dieser speichert die Zuordnung und berechnet die CRC32-Prüfsumme.

---

## 4. Nutzung in der Loxone App

Sobald das Projekt im Miniserver aktiv ist, können Sie in der **Loxone App** (iOS, Android, Web):
- **Wiedergabe steuern:** Play, Pause, Stop, Vorheriger / Nächster Titel.
- **Lautstärke:** Präzise Lautstärkeregelung (0–100 %) oder relative Tasten (`+3` / `-3`).
- **Now Playing:** Live-Anzeige von Track-Titel, Künstler, Album und Cover-Art.
- **Fortschrittsbalken:** Sekundengenaue Zeitanzeige und Scrubbing / Position-Seek.
- **Repeat & Shuffle:** Wiederholungsmodi (Aus, Gesamte Queue, Einzeltitel) und Zufallswiedergabe.
- **Raumfavoriten & Radio:** Direktes Umschalten zwischen Radiosendern und Playlists über die Favoritentasten (oder die "+"-Taste am Loxone Touch).
- **TTS & Klingel-Events:** Türklingel (Bell), Wecker, Feueralarm oder Text-to-Speech Ansagen werden direkt über die Music Assistant Player wiedergegeben.

---

## 5. Zwei Betriebsmodi

### Modus A: Standalone Bridge (Empfohlen für sofortigen Start)
Der Emulator läuft als eigener Prozess oder Docker-Container. Er verbindet sich über das Netzwerk mit Ihrem Music Assistant:
```bash
python3 -m loxone_musicserver.main --ma-url http://192.168.1.10:8095 --username admin --password "your_password"
```
Oder via Docker Compose:
```bash
docker compose up -d
```

### Modus B: Natives Music Assistant Plugin
Kopieren Sie das Verzeichnis `loxone_musicserver/provider` in das Provider-Verzeichnis Ihrer Music Assistant Installation unter:
`music_assistant/providers/loxone/`
Music Assistant erkennt das Plugin beim Neustart automatisch über `manifest.json`.
