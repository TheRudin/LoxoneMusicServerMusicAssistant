# Loxone Music Server / AudioServer – Schritt-für-Schritt Anleitung

Diese Anleitung erklärt genau, wie das System aufgebaut ist und wie Sie es in **3 einfachen Schritten** zum Laufen bringen – ohne Verwirrung um zusätzliche Container oder Home Assistant.

---

## 1. Verständnis & Architektur: Was brauche ich?

Sie müssen **KEIN zusätzliches Docker-Image starten** und **KEINE Home Assistant Integration** einrichten, wenn Sie das Plugin direkt in Music Assistant nutzen möchten!

```
┌─────────────────────────────────┐
│     Loxone Miniserver & App     │  (Bedienung & Taster)
└────────────────┬────────────────┘
                 │
                 │ Loxone Protokoll (UDP 7070, TCP 7091, TCP 7095)
                 ▼
┌──────────────────────────────────────────────────────────────┐
│  Music Assistant (auf Unraid http://192.168.1.10:8095)       │
│  └── Eingebautes Plugin: providers/loxone/                   │
│      (Gibt sich im Netzwerk als Loxone AudioServer aus)      │
└────────────────┬─────────────────────────────────────────────┘
                 │
                 ▼
┌──────────────────────────────────────────────────────────────┐
│  Ihre Lautsprecher (Sonos, AirPlay, Chromecast, etc.)        │
└──────────────────────────────────────────────────────────────┘
```

- **Music Assistant:** Läuft bereits auf Ihrem Unraid-Server (`192.168.1.10:8095`) und verwaltet Ihre Lautsprecher (z. B. Badezimmer, Wohnzimmer, MacBook).
- **Das Plugin (`providers/loxone`):** Läuft **direkt im Music Assistant**. Es lässt Ihren Music Assistant für Loxone wie einen echten Loxone AudioServer erscheinen.
- **Loxone Miniserver / Config:** Sucht im Netzwerk nach einem AudioServer und findet Ihren Music Assistant.

---

## 2. Die 3 Schritte zur Inbetriebnahme (Nativer Modus – Empfohlen)

### Schritt 1: Plugin-Ordner in den Music Assistant Container kopieren

Auf Ihrem Unraid-Server läuft Music Assistant bereits als Docker-Container. 
Öffnen Sie das **Unraid Web-Terminal** (oben rechts in Unraid auf das Terminal-Icon `>_` klicken) und führen Sie folgenden Befehl aus:

```bash
# 1. Neueste Version direkt in den Music Assistant Container kopieren:
docker exec -it music-assistant /bin/sh -c "TARGET=\$(python3 -c 'import sys; print(f\"/app/venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/music_assistant/providers/loxone\")') && rm -rf /tmp/repo && git clone https://github.com/TheRudin/LoxoneMusicServerMusicAssistant.git /tmp/repo && mkdir -p \$TARGET && cp -r /tmp/repo/providers/loxone/* \$TARGET/ && rm -rf /tmp/repo && echo 'Loxone Plugin erfolgreich aktualisiert!'"

# 2. Container neu starten:
docker restart music-assistant
```

> [!IMPORTANT]
> **Netzwerkmodus in Unraid (WICHTIG für Loxone Erkennung):**
> Der Music Assistant Docker-Container muss in Unraid auf **Network Type: Host** stehen:
> - Öffnen Sie in Unraid den Reiter **Docker**.
> - Klicken Sie auf **music-assistant** -> **Edit**.
> - Stellen Sie sicher, dass **Network Type:** auf **Host** eingestellt ist (nicht `Bridge`).
> - Klicken Sie unten auf **Apply**.
> *(Im Bridge-Modus blockiert Docker die UDP-Discovery-Broadcasts von Loxone auf Port 7070).*

> [!TIP]
> **Dauerhafte Einbindung bei Container-Updates:**
> Sie können den Ordner `providers/loxone` auf Unraid unter `/mnt/user/appdata/music-assistant/custom_providers/loxone` ablegen und in der Unraid Docker-Vorlage für Music Assistant als Pfad einbinden:
> - **Container-Pfad:** `/app/venv/lib/python3.14/site-packages/music_assistant/providers/loxone`
> - **Host-Pfad:** `/mnt/user/appdata/music-assistant/custom_providers/loxone`

---

### Schritt 2: Plugin in Music Assistant aktivieren

1. Öffnen Sie die Music Assistant Weboberfläche im Browser: `http://192.168.1.10:8095`
2. Klicken Sie links unten auf **Einstellungen (Settings)** ⚙️.
3. Wählen Sie den Reiter **Plugins**.
4. Falls **Loxone Music Server** bereits vorhanden ist: Klicken Sie auf die drei Punkte `⋮` -> **Neu laden (Reload)**. Falls noch nicht hinzugefügt: Klicken Sie auf **Plugin hinzufügen (+)** und wählen Sie **Loxone Music Server**.
5. Übernehmen Sie die Einstellungen (Servername: `MusicAssistant-AudioServer`, Ports: `7070`, `7091`, `7095`) und klicken Sie auf **Speichern**.
6. **Fertig!** Music Assistant lauscht nun sofort auf den Loxone-Ports (UDP 7070, TCP 7091, TCP 7095).

---

### Schritt 3: In Loxone Config einbinden

1. Öffnen Sie **Loxone Config** und verbinden Sie sich mit Ihrem Miniserver.
2. Klicken Sie oben im Menüband auf **Geräte suchen** (oder im Peripherie-Baum Rechtsklick auf **Audio** -> **Audio Server suchen**).
3. Der Server wird automatisch im Netzwerk gefunden:
   - **Typ:** `Audioserver`
   - **Name:** `MusicAssistant-AudioServer`
   - **IP-Adresse:** `192.168.1.10` (Port `7091`)
4. Klicken Sie auf **Gerät anlegen**.
5. Ihre Music Assistant Lautsprecher (*Badezimmer*, *Wohnzimmer*, *MacBook* etc.) werden sofort als Audio-Zonen angelegt!
6. Klicken Sie auf **In Miniserver speichern**.

> [!TIP]
> **Manuell hinzufügen (falls UDP-Broadcast in Ihrem WLAN blockiert ist):**
> Sollte Ihr PC in einem separaten WLAN/Subnetz sein und Broadcasts blockiert werden:
> 1. In Loxone Config im Peripheriebaum auf **Audio** rechtsklicken -> **Neues Gerät** -> **Audioserver**.
> 2. Bei **Adresse / Host:** `192.168.1.10:7091` eintragen.
> 3. Bei **MAC-Adresse:** Die in Music Assistant angezeigte MAC (z. B. `C9:63:F8:51:F6:AD`) eintragen.

---

## 3. Alternative: Separater Docker-Container (Standalone Bridge)

> [!NOTE]
> **Nur erforderlich, falls Sie das Plugin NICHT direkt in Music Assistant kopieren möchten.** Wenn Sie die Schritte aus Abschnitt 2 ausgeführt haben, ignorieren Sie diesen Abschnitt komplett!

Falls Sie den Emulator lieber als eigenständigen, separaten Container laufen lassen möchten:

1. Kopieren Sie `.env.example` nach `.env`:
   ```bash
   cp .env.example .env
   ```
2. Tragen Sie in `.env` Ihre Zugangsdaten ein:
   ```env
   MA_URL=http://192.168.1.10:8095
   MA_USERNAME=admin
   MA_PASSWORD=IhrPasswort
   ```
3. Starten Sie den Container:
   ```bash
   docker compose up -d
   ```

---

## 4. Lokale Verifikation aller Endpunkte

Um alle 25 Loxone-Endpunkte (Discovery, WebSockets, HTTP, Queue, Radios, Playlists) vorab zu testen:

```bash
# Startet die automatisierte Test-Suite:
python3 verify_endpoints.py
```
Alle 25 Tests müssen mit `[PASS]` abschließen.

---

## 5. Häufige Fragen (FAQ)

### Brauche ich Home Assistant?
**Nein.** Das Plugin verbindet Loxone direkt mit Music Assistant. Home Assistant wird an keiner Stelle benötigt.

### Muss ich ein separates Docker-Image starten?
**Nein.** Wenn Sie das Plugin in den `providers`-Ordner von Music Assistant legen (Schritt 1), läuft der AudioServer direkt im existierenden Music Assistant Container mit.

### Werden Passwörter auf GitHub gespeichert?
**Nein.** Alle Code-Dateien, Docker-Dateien und Git-Commits nutzen ausschließlich Platzhalter (`your_password`) oder Umgebungsvariablen (`MA_PASSWORD`). Persönliche Passwörter liegen nur in der lokalen, von Git ignorierten `.env`-Datei.
