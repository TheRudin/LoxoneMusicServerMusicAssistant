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
# 1. Ordner in den Music Assistant Container kopieren:
docker cp /mnt/user/appdata/music-assistant/providers/loxone music-assistant:/app/venv/lib/python3.12/site-packages/music_assistant/providers/loxone

# 2. Container neu starten:
docker restart music-assistant
```

> [!TIP]
> **Dauerhafte Einbindung bei Container-Updates:**
> Legen Sie den Ordner `providers/loxone` auf Unraid unter `/mnt/user/appdata/music-assistant/custom_providers/loxone` ab und tragen Sie in der Unraid Docker-Vorlage für Music Assistant einen zusätzlichen Pfad ein:
> - **Container-Pfad:** `/app/venv/lib/python3.12/site-packages/music_assistant/providers/loxone`
> - **Host-Pfad:** `/mnt/user/appdata/music-assistant/custom_providers/loxone`

---

### Schritt 2: Plugin in Music Assistant aktivieren

1. Öffnen Sie die Music Assistant Weboberfläche im Browser: `http://192.168.1.10:8095`
2. Klicken Sie links unten auf **Einstellungen (Settings)** ⚙️.
3. Wählen Sie den Reiter **Plugins**.
4. Klicken Sie auf **Plugin hinzufügen (+)**.
5. Wählen Sie **Loxone Music Server** aus.
6. Übernehmen Sie die Standardeinstellungen (Servername: `MusicAssistant-AudioServer`, Ports: `7070`, `7091`, `7095`) und klicken Sie auf **Speichern**.
7. **Fertig!** Music Assistant lauscht nun sofort auf den offiziellen Loxone-Ports.

---

### Schritt 3: In Loxone Config einbinden

1. Öffnen Sie **Loxone Config** und laden Sie Ihr Projekt.
2. Klicken Sie im Baum auf **Audio** oder oben auf **Geräte suchen**.
3. Wählen Sie **Audio Server suchen**.
4. Der Server wird sofort automatisch erkannt:
   - **Typ:** `Audioserver`
   - **Name:** `MusicAssistant-AudioServer`
   - **IP-Adresse:** `192.168.1.10` (Port `7091`)
5. Klicken Sie auf **Gerät anlegen**.
6. Ihre Music Assistant Player (*Badezimmer*, *Wohnzimmer* etc.) werden als Audio-Zonen angelegt.
7. Verknüpfen Sie die Zonen mit den Räumen und speichern Sie das Projekt in den Miniserver (**In Miniserver speichern**).

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
