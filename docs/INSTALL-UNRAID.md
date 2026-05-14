# Installation auf Unraid

AlbuMine läuft als **ein einziger Container** — Redis (die Job-Queue), der
Verarbeitungs-Worker und die Web-UI sind eingebaut. Es ist *kein* separater
Redis-Container nötig.

## Voraussetzungen

- Unraid 6.12+ mit installiertem **Community Applications** Plugin
- Ein veröffentlichtes AlbuMine-Image (siehe [Image bereitstellen](#image-bereitstellen))
- Optional: ein laufender **Ollama**-Container für lokale Vision-LLM-Verarbeitung
- Optional: NVIDIA-GPU für beschleunigte Bildverbesserung

## Image bereitstellen

Es gibt (noch) kein offiziell gehostetes Image. Bis dahin selbst bauen und in
eine Registry pushen, auf die der Unraid-Server Zugriff hat:

```bash
# Multi-Arch-Build (amd64 zwingend, arm64 optional)
docker buildx build \
  --platform linux/amd64,linux/arm64 \
  -f docker/Dockerfile \
  -t <registry>/albumine:latest \
  --push .
```

Danach in `unraid/albumine.xml` alle mit `CHANGEME` markierten Felder anpassen
(`<Repository>`, `<Registry>`, `<Support>`, `<Project>`, `<Icon>`,
`<TemplateURL>`).

## Installation über Community Applications

1. Das angepasste `albumine.xml` in
   `/boot/config/plugins/dockerMan/templates-user/` auf dem Unraid-Server
   ablegen (oder über ein eigenes CA-Repo bereitstellen).
2. In der Unraid-UI: **Apps → "AlbuMine"** suchen → **Install**.
3. Volume-Mappings prüfen/anpassen:

   | Container-Pfad | Beispiel Host-Pfad                  | Zweck                          |
   |----------------|-------------------------------------|--------------------------------|
   | `/input`       | `/mnt/user/scans/eingang/`          | Watch-Folder für Scans         |
   | `/output`      | `/mnt/user/fotos/album-digital/`    | verarbeitete Bilder            |
   | `/config`      | `/mnt/user/appdata/albumine/`       | SQLite-DB, Logs                |
   | `/archive`     | *(optional, leer lassen)*           | Original-PDFs aufbewahren      |

4. Environment-Variablen prüfen (siehe unten), dann **Apply**.
5. Web-UI über `http://<unraid-ip>:8765` öffnen.

## Environment-Variablen

| Variable                            | Default                     | Bedeutung                                   |
|-------------------------------------|-----------------------------|---------------------------------------------|
| `PUID` / `PGID` / `UMASK`           | `99` / `100` / `022`        | Benutzer-Mapping (Unraid-Standard)          |
| `ALBUMINE_WEBUI_PORT`               | `8765`                      | Port der Web-UI                             |
| `ALBUMINE_AI_PROVIDER`              | `ollama`                    | `ollama` \| `anthropic` \| `openai_compat`  |
| `ALBUMINE_OLLAMA_HOST`              | `http://localhost:11434`    | Adresse des Ollama-Servers                  |
| `ALBUMINE_OLLAMA_VISION_MODEL`      | `llava`                     | Ollama-Vision-Modell                        |
| `ALBUMINE_ANTHROPIC_API_KEY`        | *(leer)*                    | nur für `anthropic` — Bilder gehen an Anthropic |
| `ALBUMINE_DEFAULT_ENHANCEMENT_LEVEL`| `basic`                     | `none` \| `basic` \| `enhance` \| `restore` |
| `ALBUMINE_REALESRGAN_BIN`           | *(leer)*                    | Pfad zur Real-ESRGAN-CLI (für `enhance`)    |
| `ALBUMINE_GFPGAN_BIN`               | *(leer)*                    | Pfad zur GFPGAN-CLI (für `restore`)         |
| `ALBUMINE_LOG_JSON` / `ALBUMINE_LOG_LEVEL` | `true` / `INFO`      | Logging                                     |

> `ALBUMINE_REDIS_URL` muss **nicht** gesetzt werden — der Container bringt
> seine eigene Redis-Instanz mit (`redis://localhost:6379`).

## Ollama-Anbindung

Läuft Ollama als separater Unraid-Container, ist er über das Bridge-Netzwerk
unter der LAN-IP des Servers erreichbar, z. B.:

```
ALBUMINE_OLLAMA_HOST=http://192.168.0.9:11434
```

Den AI-Status prüfst du im AlbuMine-Status-Dashboard (`/status`).

## GPU-Beschleunigung (optional)

Die Bildverbesserungs-Stufen `enhance` (Real-ESRGAN) und `restore` (GFPGAN)
sind **nicht** im Image enthalten — sie sind schwer und GPU-abhängig. Um sie zu
nutzen:

1. Die jeweilige CLI im Container verfügbar machen (eigenes abgeleitetes Image
   oder ein zusätzliches Volume mit dem Binary).
2. `ALBUMINE_REALESRGAN_BIN` / `ALBUMINE_GFPGAN_BIN` auf das Binary zeigen lassen.
   Der Aufruf-Vertrag ist `<bin> -i <input> -o <output> [extra args]`.
3. Für NVIDIA-Beschleunigung in den **Extra Parameters** des Containers
   `--runtime=nvidia` ergänzen und das *Nvidia-Driver*-Plugin installieren.

Ohne diese Tools funktioniert AlbuMine weiterhin — die Verarbeitung fällt
einfach auf die `basic`-Stufe (Farb-/Kontrastkorrektur, immer verfügbar) zurück.

## Manuell (ohne CA-Template)

```bash
docker run -d --name albumine \
  -p 8765:8765 \
  -e PUID=99 -e PGID=100 -e UMASK=022 \
  -e ALBUMINE_OLLAMA_HOST=http://192.168.0.9:11434 \
  -v /mnt/user/scans/eingang:/input \
  -v /mnt/user/fotos/album-digital:/output \
  -v /mnt/user/appdata/albumine:/config \
  <registry>/albumine:latest
```

## Erste Schritte

1. Foto-Scans (Einzelbilder, PDFs oder Bildpaare `foto_001a.jpg` /
   `foto_001b.jpg`) in den `/input`-Ordner legen.
2. AlbuMine erkennt neue Dateien automatisch (Watch-Folder); alternativ in der
   Web-UI auf **Input neu einlesen** klicken.
3. Verarbeitete Paare erscheinen in der **Galerie**. Mit `needs_review`
   markierte Einträge im Detail prüfen, Daten korrigieren, ggf. neu verarbeiten.

## Troubleshooting

- **Container startet, aber Web-UI nicht erreichbar:** Logs prüfen
  (`docker logs albumine`). Die drei Prozesse (redis, worker, web) werden von
  `supervisord` verwaltet — Startfehler stehen im Log.
- **Alles landet auf `needs_review` mit Tesseract-Fallback:** Das AI-Backend ist
  nicht erreichbar. `ALBUMINE_OLLAMA_HOST` prüfen, Status-Dashboard ansehen.
- **Rechte-Probleme an den Volumes:** `PUID`/`PGID` müssen zum Eigentümer der
  Share-Verzeichnisse passen.
