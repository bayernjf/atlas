#!/bin/sh
# Atlas 容器入口（docs/30 §2）：先应用迁移，再启动主进程。
set -e

python -m scripts.ops.apply_migrations

exec "$@"
