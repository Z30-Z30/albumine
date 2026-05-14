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
| Einzelbild ohne Marker          | `single_image`  | nur Front                        |
| Bild mit Marker ohne Partner / Konflikt | `ambiguous` | `needs_review`               |

Side-Marker werden konservativ geparst: ein nacktes `a`/`b` zählt nur nach
einer Ziffer (`foto_001a`), Wort-Marker (`front`/`back`) nur nach einem
Trennzeichen — so wird z. B. `banana.jpg` nicht fälschlich als Paar erkannt.

**Idempotenz:** Jedes `ScanPair` bekommt eine `pair_id`, die aus dem
*Inhalts-Hash* (SHA-256) der Quelldateien plus den Seitenindizes abgeleitet
wird. Re-Ingest derselben Dateien ⇒ dieselbe `pair_id` ⇒ keine Duplikate.
