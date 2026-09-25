#!/bin/sh
# Atlas 容器入口（docs/30 §2、docs/31 §2.2、docs/62 §4）：先做单副本闸门，再迁移、播种账号，最后启动主进程。
#
# 闸门为什么放在最前、且宁可失败也不放行（docs/62 §2 D-6）：多进程会为同一条挂起审批帧
# 各自重建 pending 并各自起续跑线程，下游真实副作用（退款/写库）因此被执行两遍。
# 2026-09-25 实测与机制链见 docs/34 的复审更新注记；修复方案（帧一次性认领）尚未落码，见 docs/62。
set -e

reject_workers() {
    # $1 进程数，$2 来源名（报错时告诉部署者去哪儿改）
    case "$1" in
        ''|*[!0-9]*)
            echo "[atlas] 拒绝启动：$2 必须是整数（当前 '$1'）。" >&2
            exit 2
            ;;
    esac
    if [ "$1" -gt 1 ]; then
        echo "[atlas] 拒绝启动：$2=$1，Atlas 目前只能单副本运行。" >&2
        echo "[atlas] 原因：挂起审批帧会被每个启动的进程各自认领续跑一次，" >&2
        echo "[atlas]       下游真实副作用（退款/写库）被执行两遍、分支还可能相反。" >&2
        echo "[atlas] 实测与依据：docs/34 的 2026-09-25 复审更新注记；" >&2
        echo "[atlas] 解除前置条件：docs/62（帧一次性认领，落码后本闸门方可放开）。" >&2
        exit 1
    fi
}

reject_workers "${ATLAS_WORKERS:-1}" "ATLAS_WORKERS"

# CMD 被覆盖、或 docker run 直接传参时，命令行里的 workers 旗标同样拦掉（uvicorn 长/短两种写法）
prev=""
for arg in "$@"; do
    value=""
    case "$arg" in
        --workers)
            prev="workers"
            continue
            ;;
        -w)
            prev="short-workers"
            continue
            ;;
        --workers=*)
            value="${arg#--workers=}"
            ;;
        *)
            if [ "$prev" = "workers" ] || [ "$prev" = "short-workers" ]; then
                value="$arg"
            fi
            ;;
    esac
    prev=""
    if [ -n "$value" ]; then
        reject_workers "$value" "--workers"
    fi
done

python -m scripts.ops.apply_migrations
python -m scripts.ops.seed_accounts

exec "$@"
