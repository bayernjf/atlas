#!/bin/sh
# Atlas PG 备份（docs/30 §5.1）：pg_dump custom-format → backups/atlas-<utc-date>.dump
set -e

mkdir -p backups
out="backups/atlas-$(date -u +%Y%m%dT%H%M%SZ).dump"

docker compose exec -T db pg_dump -U atlas -d atlas -Fc > "$out"
echo "wrote $out"
