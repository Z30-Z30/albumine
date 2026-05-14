# Installation auf Unraid

> Skelett — wird in Phase 8 (Unraid-Template) vollständig ausgearbeitet.

## Voraussetzungen

- Unraid mit installiertem **Community Applications** Plugin
- Optional: ein laufender **Ollama**-Container für lokale Vision-LLM-Verarbeitung
- Optional: NVIDIA-GPU + Nvidia-Driver-Plugin für beschleunigtes Upscaling

## Installation (geplant)

1. In Community Applications nach **AlbuMine** suchen.
2. Volume-Mappings setzen:
   - `/input`  → z. B. `/mnt/user/scans/eingang`
   - `/output` → z. B. `/mnt/user/fotos/album-digital`
   - `/config` → z. B. `/mnt/user/appdata/albumine`
   - optional `/archive` → z. B. `/mnt/user/scans/archiv`
3. Environment-Variablen prüfen (Port, `OLLAMA_HOST`, AI-Provider).
4. Container starten, Web-UI über den gesetzten Port öffnen.

## Manuell (ohne CA-Template)

```bash
docker run -d --name albumine \
  -p 8765:8765 \
  -e PUID=99 -e PGID=100 -e UMASK=022 \
  -e ALBUMINE_OLLAMA_HOST=http://192.168.0.9:11434 \
  -v /mnt/user/scans/eingang:/input \
  -v /mnt/user/fotos/album-digital:/output \
  -v /mnt/user/appdata/albumine:/config \
  albumine:latest
```

> Hinweis: AlbuMine benötigt Redis für die Task-Queue. Auf Unraid entweder
> einen Redis-Container betreiben und `ALBUMINE_REDIS_URL` darauf zeigen lassen,
> oder das CA-Template nutzen, das die Abhängigkeit dokumentiert.
