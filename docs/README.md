# AlbuMine

Selbst-hostbare Web-App zur **Digitalisierung und Anreicherung alter Familienalben**.

AlbuMine verarbeitet Foto-Scans aus einem Watch-Folder. Kernfeature:
**Duplex-Scans** — Vorderseite (das Foto) und Rückseite (handschriftliche Notiz
mit Datum/Ort/Personen) — werden automatisch zu einer einzigen, mit Metadaten
angereicherten Bilddatei zusammengeführt.

> Status: **Phase 2 – Ingest & Pair-Detection.** Watch-Folder, PDF-Splitting
> und Front/Back-Pair-Detection stehen (mit Unit-Tests). AI-Layer und Web-UI
> folgen in den nächsten Phasen.

## Features (Zielbild)

- Watch-Folder-Ingest mit Pair-Detection (PDF-Duplex, Bildpaare, manueller Override)
- Vision-LLM-Pipeline für handschriftliche Rückseiten (Ollama / Claude / OpenAI-kompatibel)
- Robustes Datum-Parsing für unvollständige Angaben („Sommer 1962", „ca. 1970")
- Metadaten-Schreiben via ExifTool (EXIF/IPTC/XMP)
- Web-UI: Galerie, manuelle Korrektur, Status-Dashboard
- Optionale Bildverbesserung (Crop/Deskew → Farbkorrektur → Upscaling → Gesichts-Restauration)
- Unraid Community Applications Template

## Tech-Stack

| Bereich        | Wahl                                              |
|----------------|---------------------------------------------------|
| Backend        | Python 3.12 + FastAPI                             |
| Frontend       | HTMX + Jinja2 (kein Build-Step)                   |
| Task-Queue     | ARQ (Redis-basiert)                               |
| Datenbank      | SQLite (SQLModel/SQLAlchemy)                      |
| Bildverarbeitung | Pillow, OpenCV, pdf2image, ExifTool             |
| OCR            | Vision-LLM primär, Tesseract als Fallback         |
| Container      | Multi-Stage Dockerfile auf `python:3.12-slim`     |

## Entwicklung (lokal)

Voraussetzung: Docker + Docker Compose.

```bash
# Stack bauen und starten (App + Redis)
docker compose up --build

# Web-UI: http://localhost:8765
# Healthcheck: http://localhost:8765/healthz
```

Ohne Container, direkt mit Python:

```bash
pip install -e ".[dev]"
albumine            # startet uvicorn auf ALBUMINE_WEBUI_PORT (Default 8765)
pytest              # Tests
```

## Konfiguration

Alle Einstellungen kommen aus Environment-Variablen (Präfix `ALBUMINE_`).
Siehe [`config.py`](../src/albumine/config.py) für die vollständige Liste.
Wichtige Variablen:

| Variable                  | Default                  | Bedeutung                          |
|---------------------------|--------------------------|------------------------------------|
| `ALBUMINE_WEBUI_PORT`     | `8765`                   | Port der Web-UI                    |
| `ALBUMINE_REDIS_URL`      | `redis://localhost:6379` | Redis für die ARQ-Queue            |
| `ALBUMINE_AI_PROVIDER`    | `ollama`                 | `ollama` \| `anthropic` \| `openai_compat` |
| `ALBUMINE_OLLAMA_HOST`    | `http://localhost:11434` | Ollama HTTP-API                    |
| `PUID` / `PGID` / `UMASK` | `99` / `100` / `022`     | Unraid-Benutzer-Mapping            |

## Dokumentation

- [INSTALL-UNRAID.md](INSTALL-UNRAID.md) — Installation auf Unraid
- [ARCHITECTURE.md](ARCHITECTURE.md) — Architektur und Workflow

## Lizenz

MIT
