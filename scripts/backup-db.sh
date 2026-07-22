#!/usr/bin/env bash
# Nightly logical backup of the book library.
#
# Why this exists: the library was destroyed twice (2026-07-14, 2026-07-21) with
# no recoverable backup. The prose lives only in Postgres plus output/*.md, and
# a cascade DELETE from `projects` takes both. `output/backups/` held nothing
# newer than 2026-06-02 when the second wipe happened.
#
# Usage:
#   scripts/backup-db.sh                 # write a dated dump, prune old ones
#   BACKUP_KEEP=30 scripts/backup-db.sh  # keep 30 instead of the default 14
#
# Cron (daily 04:00):
#   0 4 * * * cd /path/to/bestseller && scripts/backup-db.sh >> output/backups/backup.log 2>&1
set -euo pipefail

CONTAINER="${BACKUP_DB_CONTAINER:-bestseller-db-1}"
DB_USER="${BACKUP_DB_USER:-bestseller}"
DB_NAME="${BACKUP_DB_NAME:-bestseller}"
KEEP="${BACKUP_KEEP:-14}"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${BACKUP_DIR:-$ROOT/output/backups/daily}"
mkdir -p "$DEST"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP="$DEST/${DB_NAME}-${STAMP}.dump"

if ! docker ps --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo "[backup] 容器 $CONTAINER 未运行，跳过" >&2
  exit 1
fi

echo "[backup] dumping $DB_NAME -> $DUMP"
# -Fc = custom format: compressed, and restorable selectively with pg_restore.
docker exec "$CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" -Fc > "$DUMP"

SIZE=$(wc -c < "$DUMP")
if [ "$SIZE" -lt 100000 ]; then
  echo "[backup] 中止：产出仅 ${SIZE} 字节，疑似空库或 dump 失败，保留文件供检查" >&2
  exit 2
fi

echo "[backup] ok: $(du -h "$DUMP" | cut -f1)"

# Prune, newest first.
ls -1t "$DEST"/${DB_NAME}-*.dump 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
  echo "[backup] prune $old"
  rm -f "$old"
done

echo "[backup] 现有备份 $(ls -1 "$DEST"/${DB_NAME}-*.dump 2>/dev/null | wc -l | tr -d ' ') 份，保留上限 $KEEP"
