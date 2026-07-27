#!/bin/sh
# In-stack automated backup loop (runs inside the `backup` sidecar container).
#
# Why a sidecar and not host cron: the library was wiped three times
# (2026-07-14/21/23) with no recoverable backup, because the host cron was
# never installed. A sidecar restarts with Docker and dumps on its own — no
# dependency on the host machine being awake or a crontab surviving.
#
# Connection comes from libpq env (PGHOST/PGUSER/PGPASSWORD/PGDATABASE) set by
# docker-compose. Dumps land in /backups (mounted to ./output/backups/daily).
set -eu

INTERVAL="${BACKUP_INTERVAL_SECONDS:-86400}"
KEEP="${BACKUP_KEEP:-14}"
MIN_BYTES="${BACKUP_MIN_BYTES:-100000}"
DEST="/backups"

mkdir -p "$DEST"
echo "[backup-sidecar] started; interval=${INTERVAL}s keep=${KEEP} dest=${DEST}"

while true; do
  STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
  DUMP="${DEST}/${PGDATABASE:-bestseller}-${STAMP}.dump"

  # -Fc = custom format: compressed, restorable selectively with pg_restore.
  if pg_dump -Fc > "$DUMP" 2>/tmp/pgdump.err; then
    SIZE="$(wc -c < "$DUMP" | tr -d ' ')"
    if [ "$SIZE" -lt "$MIN_BYTES" ]; then
      echo "[backup-sidecar] WARN dump only ${SIZE} bytes (< ${MIN_BYTES}); kept for inspection"
    else
      echo "[backup-sidecar] ok ${DUMP} (${SIZE} bytes)"
    fi
  else
    echo "[backup-sidecar] ERROR pg_dump failed; retrying next cycle:" >&2
    cat /tmp/pgdump.err >&2 || true
    rm -f "$DUMP"
  fi

  # Prune oldest, keep newest $KEEP.
  ls -1t "${DEST}/${PGDATABASE:-bestseller}"-*.dump 2>/dev/null \
    | tail -n +"$((KEEP + 1))" \
    | while IFS= read -r old; do
        echo "[backup-sidecar] prune ${old}"
        rm -f "$old"
      done

  sleep "$INTERVAL"
done
