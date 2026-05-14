#!/usr/bin/env bash
# Entrypoint: align the runtime user with the host's PUID/PGID (Unraid /
# linuxserver.io convention), fix volume ownership, then drop privileges.
set -euo pipefail

PUID="${PUID:-99}"
PGID="${PGID:-100}"
UMASK="${UMASK:-022}"

umask "${UMASK}"

# Create / adjust the 'albumine' group and user to match PUID/PGID.
if ! getent group "${PGID}" >/dev/null 2>&1; then
    groupadd -g "${PGID}" albumine
fi
if ! getent passwd "${PUID}" >/dev/null 2>&1; then
    useradd -u "${PUID}" -g "${PGID}" -M -s /usr/sbin/nologin albumine
fi

RUN_USER="$(getent passwd "${PUID}" | cut -d: -f1)"

# Ensure the mounted volumes are writable by the runtime user.
for dir in /input /output /config; do
    if [ -d "${dir}" ]; then
        chown "${PUID}:${PGID}" "${dir}" 2>/dev/null || true
    fi
done

echo "[entrypoint] starting as ${RUN_USER} (PUID=${PUID} PGID=${PGID} UMASK=${UMASK})"
exec gosu "${PUID}:${PGID}" "$@"
