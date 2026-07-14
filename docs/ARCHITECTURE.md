# Architektur

> Lebendiges Dokument — wird pro Phase ergänzt.

## Überblick

AlbuMine ist eine Single-Container-App (plus Redis) mit drei Schichten:

1. **Ingest** — Watch-Folder, Pair-Detection, PDF-Splitting.
2. **Processing** — Front-Bildverarbeitung, Back-OCR via Vision-LLM,
   Datum-Parsing, Metadaten-Schreiben.
3. **Web/API** — FastAPI + HTMX-UI für Galerie, Korrektur und Status.

Lang laufende Arbeit (Bildverarbeitung, AI-Calls) läuft über eine
**ARQ-Task-Queue** (Redis), damit Web-Requests nicht blockieren und die Queue
einen Neustart übersteht.

## Kern-Workflow

```
[ Watch-Folder /input ]
        │
        ▼
[ Ingest ] ──► einzelne Bilddatei | PDF (1-N Seiten) | Bild-Paar
        │
        ▼
[ Pair-Detection ] ──► Front/Back-Zuordnung (+ manueller Override im UI)
        │
        ▼
[ Front-Processing ]            [ Back-Processing ]
  Crop, Deskew, Farbe,            OCR via Vision-LLM → strukturiertes JSON
  optional Upscaling/Restore      Datum-Parsing
        │                               │
        └───────────────┬───────────────┘
                        ▼
            [ Metadaten schreiben ]  (EXIF/IPTC/XMP via ExifTool)
                        │
                        ▼
            [ Output /output ]  + DB-Eintrag mit Verarbeitungs-Historie
```

## Verzeichnisstruktur

```
src/albumine/
├── main.py            FastAPI-Entrypoint
├── config.py          Pydantic Settings (ENV-basiert)
├── logging.py         structlog-Setup
├── db/                SQLite-Modelle + Migrationen
├── ingest/            Watcher, Pair-Detector, PDF-Splitter
├── processing/        Front, Back, Enhance, Metadata-Writer
├── ai/                Provider-Interface + Ollama/Anthropic/OpenAI-kompat
├── parsing/           Datum-Parser
├── api/               REST-Endpoints
└── web/               HTMX-Templates + Static Assets
```

## Volumes

| Pfad       | Modus | Zweck                                  |
|------------|-------|----------------------------------------|
| `/input`   | rw    | Watch-Folder für eingehende Scans      |
| `/output`  | rw    | Verarbeitete Bilder                    |
| `/config`  | rw    | SQLite-DB, Settings, Logs              |
| `/archive` | rw    | optional — Original-PDFs aufbewahren   |

## Entscheidungen (ADR-Kurzform)

| Thema       | Wahl                  | Begründung                                          |
|-------------|-----------------------|-----------------------------------------------------|
| Frontend    | HTMX + Jinja2         | Kein Build-Step, schlank für eine Selfhost-App      |
| Task-Queue  | ARQ (Redis)           | Persistente Queue + Retries über Container-Neustart |
| Datenbank   | SQLite                | Pragmatisch für Single-Container-Selfhost           |
| OCR         | Vision-LLM + Tesseract-Fallback | Handschrift ist Tesseracts Schwachstelle  |
| PDF-Split   | `pypdf` (pure-python)           | Page-Counting/Splitting ohne System-Dep — testbar ohne poppler |
| Watch-Folder| `watchdog`                      | Plattformübergreifend (inotify auf Linux, FSEvents auf macOS)  |

## Ingest-Stage (Phase 2)

Der Ingest läuft zustandslos über einen **Directory-Scan**: Der `FolderWatcher`
(watchdog, debounced) feuert nach einer Ruhephase einen vollständigen Re-Scan
des `/input`-Ordners. `detect_pairs()` gruppiert die Dateien dann in
`ScanPair`-Objekte.

**Pair-Detection-Heuristik:**

| Eingabe                         | Methode         | Ergebnis                         |
|---------------------------------|-----------------|----------------------------------|
| PDF, 2 Seiten                   | `pdf_duplex`    | Seite 1 = Front, Seite 2 = Back  |
| PDF, N×2 Seiten                 | `pdf_multi`     | alternierend Front/Back          |
| PDF, 1 Seite                    | `single_pdf`    | nur Front                        |
| PDF, ungerade > 1 Seiten        | `ambiguous`     | `needs_review` — manueller Override |
| Bildpaar (`…a`/`…b`, `…_front`/`…_back`) | `image_pair` | Front + Back                  |
| Scanner-Duplex (`BASE.jpg` + `BASE_001.jpg`) | `image_pair` | Front + Back — nur wenn beide existieren; `X_001` allein bleibt Einzelbild |
| Einzelbild ohne Marker          | `single_image`  | nur Front                        |
| Bild mit Marker ohne Partner / Konflikt | `ambiguous` | `needs_review`               |

Side-Marker werden konservativ geparst: ein nacktes `a`/`b` zählt nur nach
einer Ziffer (`foto_001a`), Wort-Marker (`front`/`back`) nur nach einem
Trennzeichen — so wird z. B. `banana.jpg` nicht fälschlich als Paar erkannt.

**Idempotenz:** Jedes `ScanPair` bekommt eine `pair_id`, die aus dem
*Inhalts-Hash* (SHA-256) der Quelldateien plus den Seitenindizes abgeleitet
wird. Re-Ingest derselben Dateien ⇒ dieselbe `pair_id` ⇒ keine Duplikate.

## Metadaten-Layer (Phase 3)

**Datum-Parsing** (`parsing/date_parser.py`): wandelt Freitext von der
Rückseite (`"3.5.85"`, `"Mai '73"`, `"Sommer 1962"`, `"ca. 1970"`,
`"15. März 1980"`) in ein `ParsedDate` um — mit ISO-String in der tatsächlich
ermittelten Präzision (`YYYY-MM-DD` / `YYYY-MM` / `YYYY`), einer Confidence
(`high`/`medium`/`low`) und einem konkreten `datetime` für EXIF. Unvollständige
Angaben werden mit Defaults gefüllt: nur Jahr → `01.07.JAHR 12:00`, nur Monat →
Monatsmitte. Zweistellige Jahre werden als 19xx interpretiert (Familienalben).
Der Parser erfindet keine Präzision — `"Sommer 1962"` bleibt jahresgenau.

**Metadaten-Writer** (`processing/metadata_writer.py`): schreibt via ExifTool
in die Bilddatei. Aufgeteilt in einen reinen Argument-Builder
(`build_exiftool_args`, ohne ExifTool testbar) und den Subprozess-Aufruf
(`write_metadata`). Geschrieben werden:

| Ziel                      | Quelle                              |
|---------------------------|-------------------------------------|
| `EXIF:DateTimeOriginal`   | geparstes Datum                     |
| `IPTC:Caption-Abstract`   | Rohtext der Rückseite               |
| `IPTC:Keywords` / `XMP-dc:Subject` | Personen, Event, Ort       |
| `XMP-dc:Description`      | strukturierte Beschreibung          |
| `XMP-albumine:*`          | Confidence, AI-Provider/-Modell, Verarbeitungs-Version, Quelldateien |

Der `XMP-albumine`-Namespace wird über `exiftool_albumine.config` definiert.
IPTC wird mit `CodedCharacterSet=UTF8` geschrieben, damit Umlaute korrekt sind.
Optional kann eine `.xmp`-Sidecar-Datei mitgeschrieben werden.

## AI-Layer (Phase 4)

Der Vision-LLM-Layer (`ai/`) ist als **Strategy-Pattern** gebaut: alle Provider
implementieren `VisionProvider` und liefern dasselbe `BackExtraction`-Modell —
der Rest der Pipeline kennt das konkrete Backend nicht.

| Provider              | Backend                          | Strukturierte Ausgabe          |
|-----------------------|----------------------------------|--------------------------------|
| `OllamaProvider`      | self-hosted Ollama (HTTP)        | `format` = JSON-Schema         |
| `AnthropicProvider`   | Anthropic Claude (SDK)           | erzwungenes Tool-Use           |
| `OpenAICompatProvider`| OpenAI-kompatibel (z. B. vLLM)   | `response_format` json_schema  |

Ein einziges JSON-Schema (`ai/prompts.py:BACK_EXTRACTION_SCHEMA`) treibt die
strukturierte Ausgabe aller drei Provider und spiegelt das `BackExtraction`-
Modell. Der System-Prompt ist deutsch (Familienalben sind meist deutsch
beschriftet) und enthält Few-Shot-Beispiele.

`build_provider(settings)` wählt anhand von `ALBUMINE_AI_PROVIDER` den Provider
und prüft die nötige Konfiguration (API-Key, Base-URL).

**Wichtig — Trennung der Datums-Logik:** Das LLM liefert *seine* Lesung des
Datums (`ExtractedDate`). Die verbindliche EXIF-Zeit kommt aber weiterhin aus
dem deterministischen `date_parser`, angewendet auf `date.original_text` — die
Reconciliation passiert in der End-to-End-Pipeline (Phase 5).

**Datenschutz:** Default ist Ollama (lokal). `AnthropicProvider` sendet das
Bild an Anthropic — Cloud-Opt-in, das die UI explizit kenntlich machen muss.

## End-to-End-Pipeline (Phase 5)

`pipeline.py:Pipeline` steckt die Stufen für ein `ScanPair` zusammen:

```
process_front  ─┐
extract_back     ├─► reconcile_date ─► PhotoMetadata ─► write_metadata ─► ScanRecord (SQLite)
(Tesseract-Fb)  ─┘
```

- **`processing/front.py`** — lädt die Quelle (Bilddatei oder via `pdf2image`
  rasterisierte PDF-Seite), korrigiert EXIF-Orientierung und extrahiert das
  Foto aus dem Scan-Hintergrund (Auto-Crop + Deskew in einem Perspektiv-Warp,
  OpenCV). Farbkorrektur/Upscaling sind höhere Stufen → spätere Phase.
- **`processing/back.py`** — OCR-Orchestrator: Vision-LLM zuerst, bei Ausfall
  Tesseract-Fallback (nur Rohtext, Pair bleibt `needs_review`).
- **`reconcile_date`** — kombiniert die LLM-Datumslesung mit dem
  deterministischen `date_parser`; finale Confidence = die schwächere von beiden.
- **`db/ScanRecord`** — eine Tabelle, Primärschlüssel `pair_id` (inhalts-
  abgeleitet ⇒ Idempotenz). Status: `pending` → `processing` →
  `done` / `needs_review` / `failed`.

**Ausführungswege:**

| Weg                 | Einstieg                       | Zweck                            |
|---------------------|--------------------------------|----------------------------------|
| CLI                 | `albumine-cli` (`cli.py`)      | Einmal-Lauf, Bulk-Digitalisierung, Debugging |
| ARQ-Worker          | `albumine-worker` (`tasks.py`) | asynchrone Verarbeitung, Cron-Rescan alle 15 min |

Beide nutzen dieselbe `Pipeline`. Der Worker enqueued Process-Jobs mit einer
`pair_id`-basierten Job-ID — doppelte Jobs für dasselbe Pair werden auf
Queue-Ebene verworfen.

## Web-UI (Phase 6)

FastAPI + HTMX + Jinja2. Die Web-App **verarbeitet nicht selbst** — sie liest
aus der DB und reiht Jobs in die ARQ-Queue ein; die Arbeit macht der Worker.

| Route                          | Zweck                                            |
|--------------------------------|--------------------------------------------------|
| `GET /`                        | Galerie aller Scan-Paare                         |
| `GET /pair/{id}`               | Detail: Front/Back-Bild + Korrektur-Formular     |
| `GET /pair/{id}/image/{side}`  | Bild ausliefern (Front = Output, Back = Quelle)  |
| `POST /pair/{id}/correct`      | manuelle Korrektur → DB + Metadaten neu schreiben |
| `POST /pair/{id}/reprocess`    | erzwungenes Re-Processing einreihen              |
| `POST /rescan`                 | Input-Ordner neu einlesen                        |
| `GET /status` + `/status/ai-health` | Status-Dashboard (Queue, Fehler, AI-Health) |

Module: `api/deps.py` (geteilte Dependencies + Templates), `api/gallery.py`,
`api/actions.py`, `api/status.py`. Die App-weiten Objekte (DB-Session-Factory,
Pipeline, Redis-Pool, AI-Provider, Watcher) werden einmalig im
`main.py:lifespan` erstellt und auf `app.state` abgelegt.

**Manuelle Korrektur:** `Pipeline.apply_manual_correction` aktualisiert die
DB-Felder, re-parsed den Datums-Freitext und schreibt die Metadaten *in das
bestehende Output-Bild* zurück — ohne AI oder Front-Processing erneut zu
laufen. Re-Processing dagegen läuft die volle Pipeline (inkl. AI) neu.

**Watch-Folder-Integration:** Der `FolderWatcher` (im `lifespan` gestartet)
reiht bei Änderungen einen `scan_input_task` in die Queue ein.

**Resilienz:** Redis ist optional. Ist es offline, läuft die App im
Degraded-Modus weiter — Galerie und Korrekturen funktionieren, nur die
Queue-Aktionen (Re-Processing, Rescan) melden „Redis offline". htmx ist lokal
gebündelt (`static/htmx.min.js`), kein CDN-Zugriff nötig.

## Settings-Panel & i18n (Phase 10)

**Konfigurations-Layering:** Die Basis ist die ENV-basierte `Settings`
(Pydantic). Darüber liegt ein DB-Overlay: `AppSetting`-Zeilen (Key-Value),
geschrieben vom Web-Settings-Panel. `db/settings_store.py:effective_settings`
merged beides — die String-Overrides werden von Pydantic auf die typisierten
Felder gecoerct; ungültige Overrides werden ignoriert (die App muss immer
starten können).

`EDITABLE_SETTINGS` ist die Registry der editierbaren Felder mit Metadaten
(Kategorie, Typ, `secret`, `restart_required`). **Live wirksam** (die Pipeline
löst pro Job neu auf): Sprache, Bildverbesserung, JPEG-Qualität, Auto-Crop,
Fallback, Enhancement-Tool-Pfade. **Neustart nötig** (einmalig beim Start
aufgelöst): AI-Provider, Pfade, Ports, Redis, Logging. `config_dir` ist *nicht*
editierbar — dort liegt die Datenbank selbst (Bootstrap-Henne-Ei-Problem).

Secrets (API-Keys) werden nie an den Browser zurückgegeben; ein leeres
Secret-Feld beim Speichern heißt „Wert behalten".

**i18n** (`i18n.py`): flache JSON-Dateien in `web/translations/<code>.json`,
eine pro Sprache (16 ausgeliefert; neue Sprache = neue Datei + Eintrag in
`LANGUAGES`). Keine gettext/Babel-Abhängigkeit. Fehlende Keys fallen auf
Englisch zurück, dann auf den Key selbst. Ein Jinja-Context-Processor
(`api/deps.py`) injiziert `t`, `lang` und `text_dir` in jedes Template; Route-
Handler bekommen `t` über `Depends(get_translator)`. Die aktive Sprache kommt
aus dem `ui_language`-Override (DB), sonst aus der ENV-Basis.

## Enhancement-Pipeline (Phase 7)

`processing/enhance.py:apply_enhancement` wird in der Pipeline nach dem
Front-Processing (Crop/Deskew) angewendet. Die Stufen bauen aufeinander auf:

| Stufe     | Was passiert                                  | Werkzeug                |
|-----------|-----------------------------------------------|-------------------------|
| `none`    | nichts (nur Crop/Deskew aus dem Front-Step)   | —                       |
| `basic`   | Gray-World-Weißabgleich, leichtes Entrauschen, Kontrast-Autostretch | Pillow/OpenCV |
| `enhance` | `basic` + Upscaling                           | Real-ESRGAN (CLI)       |
| `restore` | `enhance` + Gesichts-Restauration             | GFPGAN (CLI)            |

`basic` ist immer verfügbar (kein Extra-Tooling) und der Default. Real-ESRGAN
und GFPGAN sind schwere ML-Tools — sie werden, wie im Spec gefordert, als
**externe CLI-Subprozesse** aufgerufen (Vertrag: `<bin> -i <in> -o <out>
[args]`), konfiguriert über `ALBUMINE_REALESRGAN_BIN` / `ALBUMINE_GFPGAN_BIN`.
Sie sind **nicht** im Basis-Image gebündelt (Größe/GPU-Abhängigkeit).

**Graceful Degradation:** Ist ein Tool nicht konfiguriert oder schlägt fehl,
fällt `apply_enhancement` auf die höchste tatsächlich erreichte Stufe zurück
und gibt diese zurück — die *angewendete* Stufe (nicht die angeforderte) landet
in `ScanRecord.enhancement_level` und im XMP-`albumine`-Namespace.

Die Stufe ist pro Foto wählbar: das Detail-UI bietet sie beim Re-Processing an,
die CLI über `albumine-cli scan --level <stufe>`, der globale Default kommt aus
`ALBUMINE_DEFAULT_ENHANCEMENT_LEVEL`.

## Deployment (Phase 8)

Es gibt zwei Betriebsarten desselben Images:

**All-in-one (Default, für Unraid):** `supervisord` startet Redis, den
ARQ-Worker und die Web-App in *einem* Container — ein-Klick-Installation, kein
separater Redis-Container nötig. supervisord läuft als root und droppt jeden
Prozess via `user=albumine` auf den unprivilegierten Benutzer (aus PUID/PGID).
`docker/Dockerfile` `CMD` ist dieser Modus.

**Getrennte Services (für die lokale Entwicklung):** `docker-compose.yml`
überschreibt `command:` pro Service und fährt App, Worker und Redis als drei
separate Container hoch.

`unraid/albumine.xml` ist das Community-Applications-Template — Volumes
(`/input`, `/output`, `/config`, optional `/archive`), alle ENV-Variablen,
WebUI-Port und Kategorie `MediaApp:Photos Tools:`. Die Web-App verbindet sich
mit konfigurierbarer Retry-Zahl (`ALBUMINE_REDIS_CONNECT_RETRIES`) zu Redis und
läuft ohne Redis im Degraded-Modus weiter. Details: `docs/INSTALL-UNRAID.md`.
