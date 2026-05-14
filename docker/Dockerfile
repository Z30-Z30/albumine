# syntax=docker/dockerfile:1

# --- Stage 1: build wheels --------------------------------------------------
FROM python:3.12-slim AS builder

WORKDIR /build
RUN pip install --no-cache-dir build

COPY pyproject.toml ./
COPY src ./src
COPY docs/README.md ./docs/README.md

RUN python -m build --wheel --outdir /wheels


# --- Stage 2: runtime -------------------------------------------------------
FROM python:3.12-slim AS runtime

# System dependencies for image processing and metadata.
#   libimage-exiftool-perl  -> metadata writing (EXIF/IPTC/XMP)
#   tesseract-ocr           -> OCR fallback for back-of-photo text
#   poppler-utils           -> PDF -> image conversion (pdf2image)
#   redis-server            -> ARQ queue broker (all-in-one container)
#   supervisor              -> runs redis + worker + web in one container
#   gosu                    -> drop privileges to PUID/PGID at entrypoint
RUN apt-get update && apt-get install -y --no-install-recommends \
        libimage-exiftool-perl \
        tesseract-ocr \
        tesseract-ocr-deu \
        poppler-utils \
        redis-server \
        supervisor \
        gosu \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl && rm -rf /wheels

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/supervisord.conf /etc/supervisor/albumine.conf
RUN chmod +x /usr/local/bin/entrypoint.sh

# Unraid / linuxserver.io conventions.
ENV PUID=99 \
    PGID=100 \
    UMASK=022 \
    ALBUMINE_WEBUI_PORT=8765 \
    ALBUMINE_INPUT_DIR=/input \
    ALBUMINE_OUTPUT_DIR=/output \
    ALBUMINE_CONFIG_DIR=/config

VOLUME ["/input", "/output", "/config"]
EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS "http://localhost:${ALBUMINE_WEBUI_PORT}/healthz" || exit 1

# Default: all-in-one mode (Redis + ARQ worker + web app via supervisord) for a
# one-click Unraid install. docker-compose overrides this per service.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["supervisord", "-c", "/etc/supervisor/albumine.conf"]
