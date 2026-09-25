#!/bin/sh
# Atlas PG 备份（docs/30 §5.1 / docs/65 K-B）：pg_dump custom-format → backups/atlas-<utc-date>.dump
#
# 轮转：ATLAS_BACKUP_KEEP（默认 7）——备份完成后只保留最新 N 份，其余 backups/atlas-*.dump 删除。
# 异地：ATLAS_BACKUP_REMOTE_DIR（可选）——非空时把最新 dump 拷贝到该目录（mkdir -p 自动建）。
# --dry-run：只打印"将保留/将删除/将拷贝"清单，不执行备份与删除。
set -e

DRY_RUN=0
case "$1" in
    --dry-run) DRY_RUN=1 ;;
    "" ) ;;
    *) echo "usage: $0 [--dry-run]" >&2; exit 2 ;;
esac

KEEP=${ATLAS_BACKUP_KEEP:-7}
REMOTE_DIR=${ATLAS_BACKUP_REMOTE_DIR:-}
mkdir -p backups

if [ "$DRY_RUN" = 1 ]; then
    echo "dry-run: KEEP=$KEEP REMOTE_DIR=${REMOTE_DIR:-<unset>}"
    for f in $(ls backups/atlas-*.dump 2>/dev/null | sort); do
        echo "dry-run: would keep $f"
    done
    echo "dry-run: no backup taken, no files deleted"
    exit 0
fi

out="backups/atlas-$(date -u +%Y%m%dT%H%M%SZ).dump"
docker compose exec -T db pg_dump -U atlas -d atlas -Fc > "$out"
echo "wrote $out"

# 轮转：按 UTC 时间戳文件名排序，删除超出 KEEP 的最旧备份。
# （BSD head 不支持负数行数，用计数法；保持 POSIX sh 兼容。）
total=$(ls backups/atlas-*.dump 2>/dev/null | wc -l | tr -d ' ')
excess=$((total - KEEP))
if [ "$excess" -gt 0 ]; then
    for f in $(ls backups/atlas-*.dump | sort | head -n "$excess"); do
        echo "pruning $f"
        rm -f "$f"
    done
fi

# 异地拷贝：仅最新一份。
if [ -n "$REMOTE_DIR" ]; then
    mkdir -p "$REMOTE_DIR"
    latest=$(ls backups/atlas-*.dump | sort | tail -n 1)
    cp -a "$latest" "$REMOTE_DIR/"
    echo "copied $latest -> $REMOTE_DIR/"
fi
