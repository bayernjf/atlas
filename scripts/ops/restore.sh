#!/bin/sh
# Atlas PG 恢复（docs/30 §5.1）：pg_restore --clean --if-exists，覆盖当前库。
# 用法：scripts/ops/restore.sh <dump 文件>
set -e

if [ $# -ne 1 ]; then
    echo "usage: $0 <dump file>" >&2
    exit 2
fi
dump=$1
if [ ! -f "$dump" ]; then
    echo "dump file not found: $dump" >&2
    exit 2
fi

printf "this will overwrite the current database with %s\n" "$dump"
printf "type 'yes-restore' to continue: "
read -r answer
if [ "$answer" != "yes-restore" ]; then
    echo "aborted"
    exit 1
fi

# pg_restore 对已存在对象可能输出非致命错误，不使用 set -e 的退出码即停。
docker compose exec -T db pg_restore \
    --clean --if-exists --no-owner \
    -U atlas -d atlas < "$dump"

echo "restored from $dump"
