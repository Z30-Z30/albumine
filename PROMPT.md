# AlbuMine — Claude Code Projekt-Prompt

## Projektziel

Baue eine selbst-hostbare Web-Applikation namens **AlbuMine** zur Digitalisierung und Anreicherung alter Familienalben. Die App läuft als Docker-Container auf einem Unraid-Server (installierbar über Community Applications) und verarbeitet Foto-Scans aus einem gemounteten Verzeichnis. Kernfeature: **Duplex-Scans** (Vorderseite = Foto, Rückseite = handschriftliche Notiz mit Datum/Ort/Personen) werden automatisch zu einer einzigen, mit Metadaten angereicherten Bilddatei zusammengeführt.

## Tech-Stack (Vorschlag, bitte begründet bestätigen oder ablehnen)

- **Backend**: Python 3.12 + FastAPI (async, gut für Long-Running-Tasks)
- **Frontend**: minimal — entweder HTMX + Jinja2 oder ein schlanker React/Vue SPA. Default: HTMX, da weniger Build-Overhead für eine Selfhost-App.
- **Task-Queue**: ARQ (Redis-basiert) oder FastAPI BackgroundTasks für den Start; bei Bildverarbeitungs-Bottleneck auf Celery/RQ ausweichen.
- **Datenbank**: SQLite via SQLModel/SQLAlchemy (für Selfhost-Setups deutlich pragmatischer als Postgres).
- **Bildverarbeitung**: Pillow, OpenCV, `pdf2image` (PDF → PIL), `piexif` oder `exiftool` (über `pyexiftool`) für EXIF/IPTC/XMP.
- **OCR / Texterkennung Rückseite**: Tesseract (`pytesseract`) als Fallback **plus** Vision-LLM via Ollama (z.B. `llava`, `llama3.2-vision`, `minicpm-v`) als primäre Methode — Handschrift ist Tesseracts Schwachstelle.
- **AI-Backends (austauschbar via Strategy-Pattern)**:
  - Ollama lokal (HTTP-API, Host konfigurierbar)
  - Anthropic Claude (claude-opus-4-7 als Default für Vision/Reasoning)
  - OpenAI-kompatible Endpoints (für lokale vLLM-Setups oder andere Provider)
- **Upscaling**: Real-ESRGAN oder GFPGAN via separater Pipeline (CLI-Aufruf in Subprozess), optional GPU-Beschleunigung.
- **Container**: Multi-Stage Dockerfile, Base-Image `python:3.12-slim`, ExifTool + Tesseract + Poppler als System-Deps.

## Kern-Workflow

```
[ Watch-Folder /input ]
        │
        ▼
[ Ingest ]  ──► erkennt: einzelne Bilddatei, PDF (1-N Seiten), Bild-Paar
        │
        ▼
[ Pair-Detection ]
   • PDF mit 2 Seiten     → Seite 1 = Front, Seite 2 = Back
   • PDF mit N×2 Seiten   → alternierend Front/Back
   • Zwei Bilder mit Namens-Konvention (foto_001a.jpg / foto_001b.jpg)
   • Manueller Override per Web-UI
        │
        ▼
[ Front-Processing ]                  [ Back-Processing ]
   • Auto-Crop (Rand/Hintergrund)        • OCR via Vision-LLM
   • Deskew / Rotation                     → strukturiertes JSON:
   • Farbkorrektur / Entstauben              { date, location, people,
   • Optional: Upscaling                       event, raw_text }
   • Optional: Kolorierung               • Datum-Parsing (verschiedene
                                           Formate: "Sommer 1962",
                                           "3.5.85", "Mai '73" …)
        │                                        │
        └────────────────┬───────────────────────┘
                         ▼
              [ Metadaten schreiben ]
   • EXIF DateTimeOriginal  ← geparstes Datum
   • IPTC Caption           ← raw_text von Rückseite
   • IPTC Keywords          ← people, event, location
   • XMP dc:description     ← strukturierte Beschreibung
   • XMP Custom Namespace   ← AlbuMine-spezifisch (Confidence-Scores, AI-Modell, etc.)
                         │
                         ▼
              [ Output /output ]
   • Front-Bild als JPG/TIFF mit Metadaten
   • Optional: Sidecar XMP-Datei
   • Optional: Original-PDF als _archive/ aufheben
   • Datenbank-Eintrag mit Verarbeitungs-Historie
```

## Funktionale Anforderungen

### Must-Have (MVP)

1. **Watch-Folder** auf `/input` mit Inotify; manuelles Re-Scan über UI.
2. **Pair-Detection** für die o.g. drei Fälle, mit klarer Heuristik und manuellem Override.
3. **Vision-LLM-Pipeline** für Rückseiten-Extraktion mit austauschbarem Provider (Ollama / Claude / OpenAI-kompat).
4. **Strukturiertes Datum-Parsing** — robust gegen unvollständige Angaben ("ca. 1970", "Frühling 65"). Wenn nur Jahr bekannt: `01.07.JAHR 12:00` als Default, Confidence im XMP vermerken.
5. **Metadaten-Schreiben** via ExifTool (mehr Formate als piexif, schreibt auch in TIFF/PNG/HEIC).
6. **Web-UI** mit:
   - Galerie-Ansicht der verarbeiteten Paare (Vorderseite + Rückseite nebeneinander)
   - Manuelle Korrektur extrahierter Daten
   - Re-Processing eines Paares
   - Status-Dashboard (Queue, Fehler, AI-Backend-Health)
7. **Konfiguration über Environment-Variablen** (Unraid-Standard) — keine zwingenden Config-Files.
8. **Logs strukturiert** (JSON oder zumindest mit klaren Levels), bei Fehlern keine Endlos-Retries.

### Should-Have

- **Bildverbesserung-Stufen** (auswählbar pro Foto oder als Default):
  - `none` — nur Crop/Deskew
  - `basic` — Farbkorrektur, Kontrast, Entrauschen
  - `enhance` — + Upscaling (Real-ESRGAN)
  - `restore` — + Gesichts-Restauration (GFPGAN)
- **Batch-Operationen** im UI (mehrere Paare auswählen, Aktion anwenden).
- **Export-Funktion** als ZIP mit Sidecar-XMPs oder direkter Push in einen Cloud-Folder (Nextcloud WebDAV — optional, da Ahmed eh NC AIO laufen hat).

### Could-Have / später

- Gesichts-Clustering (welche Person taucht in welchen Fotos auf) — DeepFace oder InsightFace, alles lokal.
- Geocoding der erkannten Orte (Nominatim, lokal hostbar).
- Album-Konzept (Fotos zu Gruppen zusammenfassen, Cover, Beschreibung).
- IIIF-Endpoint für Archivar:innen.
- Integration mit Paperless-ngx (für Dokumente aus dem gleichen Scan-Workflow).

## Unraid-Spezifika (wichtig!)

1. **Volumes**:
   - `/input` (rw) — Watch-Folder für eingehende Scans
   - `/output` (rw) — verarbeitete Bilder
   - `/config` (rw) — SQLite-DB, Settings, Logs
   - optional `/archive` (rw) — Original-PDFs zur Aufbewahrung
2. **PUID / PGID / UMASK** als ENV-Vars respektieren (linuxserver.io-Konvention) — `nobody:users` (99:100) ist Unraid-Default.
3. **WebUI-Port** über ENV konfigurierbar, Default `8765`.
4. **Ollama-Anbindung**: per ENV `OLLAMA_HOST=http://192.168.0.9:11434`. Wenn Ollama als separater Unraid-Container läuft, klappt das über das Unraid-Bridge-Netzwerk.
5. **GPU-Passthrough**: optional, wenn NVIDIA-Karte vorhanden — Container muss mit `--runtime=nvidia` lauffähig sein, aber auch ohne GPU funktional bleiben (Real-ESRGAN auf CPU ist langsam, aber geht).
6. **Healthcheck** im Dockerfile.
7. **Liefere ein Unraid Community Applications Template** (`albumine.xml`) mit:
   - Korrekten Volume-Bindings mit `/mnt/user/...`-Beispielen
   - Allen ENV-Vars dokumentiert
   - Icon-URL und WebUI-URL gesetzt
   - Kategorie: `MediaApp:Photos Tools:`
   - Siehe https://docs.unraid.net/de/unraid-os/using-unraid-to/run-docker-containers/community-applications/ für aktuelle Template-Spezifikation
8. **Multi-Arch**: amd64 zwingend, arm64 nice-to-have.

## Nicht-funktionale Anforderungen

- **Sicherheit**: keine Auth im MVP (Annahme: läuft im LAN hinter Reverse Proxy mit Authentik/Authelia). Aber: kein blindes Lesen von `/etc/passwd` o.ä. — keep it tight.
- **Idempotenz**: Re-Processing einer Datei darf keine Duplikate erzeugen. Hash-basierte Deduplizierung im Ingest.
- **Resilienz**: Bricht ein AI-Backend weg, läuft Verarbeitung mit Tesseract-Fallback weiter und markiert die Datei für späteres Re-Processing.
- **Datenschutz**: Default ist Ollama lokal. Cloud-LLMs nur opt-in, mit klarem Hinweis im UI ("Dieses Foto wird zur Verarbeitung an Anthropic gesendet").
- **Tests**: pytest, mind. die Pair-Detection, Datum-Parsing und Metadaten-Schreiber sollten Unit-Tests haben.

## Projektstruktur (Vorschlag)

```
albumine/
├── src/albumine/
│   ├── __init__.py
│   ├── main.py                  # FastAPI-App-Entrypoint
│   ├── config.py                # Pydantic Settings
│   ├── db/
│   │   ├── models.py
│   │   └── migrations/
│   ├── ingest/
│   │   ├── watcher.py           # Inotify-Watcher
│   │   ├── pair_detector.py
│   │   └── pdf_splitter.py
│   ├── processing/
│   │   ├── front.py             # Crop, Deskew, Color
│   │   ├── back.py              # OCR-Orchestrator
│   │   ├── enhance.py           # Real-ESRGAN, GFPGAN Wrapper
│   │   └── metadata_writer.py   # ExifTool-Wrapper
│   ├── ai/
│   │   ├── base.py              # Provider-Interface
│   │   ├── ollama.py
│   │   ├── anthropic.py
│   │   ├── openai_compat.py
│   │   └── prompts.py           # System-Prompts für Vision-Extraction
│   ├── parsing/
│   │   └── date_parser.py       # robustes Datum-Parsing
│   ├── api/                     # REST-Endpoints
│   └── web/                     # HTMX-Templates / Static Assets
├── tests/
├── docker/
│   ├── Dockerfile
│   └── entrypoint.sh
├── unraid/
│   └── albumine.xml             # CA-Template
├── docs/
│   ├── README.md
│   ├── INSTALL-UNRAID.md
│   └── ARCHITECTURE.md
├── pyproject.toml
└── docker-compose.yml           # für lokale Entwicklung
```

## Vorgehen

Arbeite **iterativ in Phasen**, nach jeder Phase Commit + kurze Zusammenfassung:

1. **Setup**: Projektstruktur, Dockerfile (lauffähig, leerer FastAPI-Endpoint), docker-compose, README-Skelett.
2. **Ingest + Pair-Detection**: ohne AI, nur File-Handling und PDF-Splitting, mit Tests.
3. **Metadaten-Layer**: ExifTool-Wrapper, Datum-Parser, Unit-Tests.
4. **AI-Layer**: Provider-Interface, Ollama-Implementation, Anthropic-Implementation. Mit klarem Vision-Prompt, der **strukturiertes JSON** zurückgibt (Schema im Code definieren, im Prompt als few-shot examples).
5. **End-to-End-Pipeline**: alles zusammenstecken, erst CLI-tauglich, dann mit BackgroundTasks.
6. **Web-UI**: Galerie, manuelle Korrektur, Status.
7. **Enhancement-Pipeline**: Upscaling, optional GFPGAN.
8. **Unraid-Template** + INSTALL-Doku.
9. **Polishing**: Logs, Errors, README mit Screenshots.

## Vision-LLM Prompt (initial, im Code als Konstante)

Der Prompt an das Vision-LLM für die Rückseite soll **strikt JSON** zurückgeben (`response_format` bei OpenAI-kompatiblen APIs, Tool-Use bei Claude, JSON-Mode bei Ollama). Schema in etwa:

```json
{
  "raw_text": "string — wortwörtliche Transkription, auch unleserliche Teile mit [?]",
  "date": {
    "iso": "YYYY-MM-DD oder YYYY-MM oder YYYY, oder null",
    "original_text": "string wie auf der Rückseite",
    "confidence": "high|medium|low"
  },
  "location": "string oder null",
  "people": ["array of strings, einzelne Namen"],
  "event": "string oder null (z.B. 'Hochzeit', 'Sommerferien')",
  "notes": "string oder null — alles was sonst noch dort steht"
}
```

System-Prompt-Idee (deutsch, da Familienalben i.d.R. deutschsprachig beschriftet):

> Du extrahierst Informationen von der Rückseite eines analogen Fotos. Die Beschriftung ist meist handschriftlich, oft in alter deutscher Schreibschrift (Sütterlin/Kurrent kommt vor), manchmal auf Schweizerdeutsch oder Mundart. Sei vorsichtig mit Datumsangaben — interpretiere niemals etwas hinein. Wenn etwas unklar ist, markiere es mit `[?]` und setze Confidence auf `low`. Antworte ausschließlich mit JSON nach dem vorgegebenen Schema.

## Was ich von dir (Claude Code) erwarte

- **Frag nach**, wenn Entscheidungen Trade-offs haben (z.B. ARQ vs. Celery, HTMX vs. React). Begründe deine Empfehlung kurz, lass mich wählen.
- **Schreib Code, der lesbar ist**, keine Cleverness um der Cleverness willen. Typ-Hints überall, docstrings wo's nicht trivial ist.
- **Halte Dependencies schlank** — jede neue Lib muss sich rechtfertigen.
- **Logs auf Deutsch oder Englisch** — sei konsistent, ich bevorzuge Englisch für Tech-Logs, Deutsch fürs UI.
- **README iterativ pflegen** — nicht erst am Ende.
- **Keine Mock-Implementations** committen, die so aussehen, als würden sie funktionieren. Lieber `raise NotImplementedError` mit klarem TODO.

Los geht's mit Phase 1.
