#!/bin/sh
# Atlas 容器入口（docs/30 §2、docs/31 §2.2）：先迁移，再播种账号，然后启动主进程。
set -e

python -m scripts.ops.apply_migrations
python -m scripts.ops.seed_accounts

exec "$@"
