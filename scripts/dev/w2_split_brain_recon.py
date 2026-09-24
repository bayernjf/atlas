"""单实例硬约束勘察：`--workers 2` 下逐条测进程内状态的失败面。

证据用于 docs/34 的 2026-09-25 复审更新注记（两份上线评审此前均未登记该阻断项）。
本脚本**只发 HTTP、不改任何产品代码**。

方法学（三条，缺一条结论就不成立）：

1. **每个请求新开连接**（`max_keepalive_connections=0`）。HTTP keep-alive 会把一整串请求
   钉在同一个 worker 上，那样测出来的是「全绿」而不是「单实例语义成立」——上一版就踩了这个坑。
2. **先测路由分布，再信其它探针**：探针 2（灰度配置可见性）是已知必然分裂的对照，
   若它 40 次全落同一侧，说明分发没发生，后面所有「不分裂」都只配记为「未采到」。
3. **区分两种成因**：探针 5 的失败是**多实例专属**（打到非执行 worker），探针 4 的失败
   在单实例下也成立（`cancellation_broker.register` 只出现在流式运行路径 `main.py:2696`，
   sync `/run` 从不登记句柄）。把后者当成多实例缺陷是错的，单列出来。

跑法（仓库根；用独立勘察库，别污染 dev 的 atlas 库）：

    docker exec atlas-pg psql -U atlas -d postgres -c "CREATE DATABASE atlas_w2_recon OWNER atlas"
    DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas_w2_recon \\
      .venv/bin/python scripts/ops/apply_migrations.py
    ATLAS_STORAGE_BACKEND=pg \\
      DATABASE_URL=postgresql+psycopg://atlas:atlas@localhost:5432/atlas_w2_recon \\
      .venv/bin/uvicorn atlas.api.main:app --host 127.0.0.1 --port 8211 --workers 2
    ATLAS_RECON_BASE=http://127.0.0.1:8211 .venv/bin/python scripts/dev/w2_split_brain_recon.py

`--workers N` 只影响日志标题，脚本不启动服务。退出码恒 0：这是勘察，不是门。
"""

from __future__ import annotations

import argparse
import os
import threading
import time
from collections import Counter
from typing import Any

import httpx

BASE = os.environ.get("ATLAS_RECON_BASE", "http://127.0.0.1:8211")
SAMPLES = 40

# trigger -> human_approval（挂起 600s），两个出口各落一条进程内消息
GRAPH: dict[str, Any] = {
    "version": 1,
    "variables": [],
    "nodes": [
        {
            "id": "trigger-1",
            "type": "trigger",
            "name": "触发",
            "description": "",
            "position": {"x": 80, "y": 200},
            "config": {"triggerType": "manual"},
        },
        {
            "id": "approval-1",
            "type": "human_approval",
            "name": "审批",
            "description": "",
            "position": {"x": 360, "y": 200},
            "config": {
                "summary": "勘察审批挂起",
                "approver": "",
                "timeoutSeconds": 600,
                "onTimeout": "reject",
                "approvedTarget": "ok-1",
                "rejectedTarget": "no-1",
            },
        },
        {
            "id": "ok-1",
            "type": "tool_call",
            "name": "通过消息",
            "description": "",
            "position": {"x": 640, "y": 120},
            # Graph v1 的 params 是 JSON 字符串（见 template/graphs.py），传 dict 会 500
            "config": {
                "tool": "message/send",
                "params": '{"channel":"sms","to":"recon","subject":"ok","body":"ok"}',
            },
        },
        {
            "id": "no-1",
            "type": "tool_call",
            "name": "拒绝消息",
            "description": "",
            "position": {"x": 640, "y": 300},
            "config": {
                "tool": "message/send",
                "params": '{"channel":"sms","to":"recon","subject":"no","body":"no"}',
            },
        },
    ],
    "edges": [
        {"id": "e1", "source": "trigger-1", "target": "approval-1"},
        {"id": "e2", "source": "approval-1", "target": "ok-1"},
        {"id": "e3", "source": "approval-1", "target": "no-1"},
    ],
}


def report(
    title: str,
    expectation: str,
    buckets: Counter,
    split_keys: tuple[str, ...],
    *,
    conclusive: bool = True,
    cause: str = "多实例专属",
) -> bool:
    """打印一个探针的分桶结果；出现任一 split_key 即判定该状态跨 worker 不可见。"""
    line = "  ".join(f"{key}×{count}" for key, count in sorted(buckets.items(), key=lambda kv: str(kv[0])))
    total = sum(buckets.values()) or 1
    worst = max((count for key, count in buckets.items() if str(key) in split_keys), default=0)
    split = worst > 0
    if not conclusive:
        flag = "未采到"
    else:
        flag = "SPLIT" if split else "ok   "
    print(f"[{flag}] {title}")
    print(f"        单实例语义期望：{expectation}")
    print(f"        实测 {total} 次：{line}（{cause}；分裂占比 {worst}/{total}）")
    return split and conclusive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=2, help="服务端实际 worker 数，仅用于日志标题")
    args = parser.parse_args()
    print(f"=== docs/34 单实例硬约束勘察（服务端 --workers {args.workers}，BASE={BASE}）===\n")

    # 每请求新建连接：keep-alive 会把请求钉死在单个 worker 上，测不出分发
    limits = httpx.Limits(max_keepalive_connections=0, max_connections=8)
    client = httpx.Client(base_url=BASE, timeout=30.0, limits=limits)

    login = client.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
    login.raise_for_status()
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    print("0) 登录 OK（PG 档会话在 iam_sessions 表，跨 worker 共享——这是唯一已知共享态）")

    results: list[bool] = []

    # 1) /metrics 只报本进程的 TenantRegistry 快照（冷启后才会显出 0/1 混合）
    gauges: Counter = Counter()
    for _ in range(SAMPLES):
        body = client.get("/metrics").text
        value = next(
            (line.split()[-1] for line in body.splitlines() if line.startswith("atlas_tenants_active")),
            "?",
        )
        gauges[value] += 1
    print("1) GET /metrics 的 atlas_tenants_active 分布")
    print(f"        {dict(gauges)}（>1 个取值＝Prometheus 单次抓取只覆盖一个 worker；"
          "两个 worker 都被暖过后各自都是 1，故本项复跑前需重启服务）\n")

    graph_id = None
    save = client.post("/api/graphs", json=GRAPH)
    if save.status_code >= 400:
        print(f"建图失败 {save.status_code}：{save.text[:400]}")
        raise SystemExit(1)
    graph_id = save.json()["id"]

    # 2) 分布自检：RoutingStore 是进程内态，PUT 后必然只在其中一个 worker 可见
    client.put(
        f"/api/graphs/{graph_id}/rollout",
        json={"strategy": "progressive", "rules": [], "inFlightPolicy": "pin-to-version"},
    ).raise_for_status()
    rollout = Counter(
        "已存" if r.json()["config"] is not None else "null"
        for r in (client.get(f"/api/graphs/{graph_id}/rollout") for _ in range(SAMPLES))
    )
    distributed = report(
        "分布自检＋灰度配置可见性（routing_store 进程内）",
        "PUT 之后每一次 GET 都应返回已存 config",
        rollout,
        ("null",),
    )
    if not distributed:
        print("        ⚠ 请求未分发到第二个 worker，后续所有「不分裂」只配记为未采到\n")
    else:
        print(f"        分发已发生：{'/'.join(f'{k}×{v}' for k, v in sorted(rollout.items()))}\n")

    conclusive = distributed

    def blocked_run(holder: dict[str, Any], stream: bool = False) -> threading.Thread:
        def run() -> None:
            try:
                if stream:
                    with httpx.stream(
                        "POST",
                        f"{BASE}/api/graphs/{graph_id}/run/stream",
                        json={},
                        headers=client.headers,
                        timeout=700.0,
                    ) as response:
                        holder["status"] = response.status_code
                        for _ in response.iter_lines():  # 持续消费直至审批被处理
                            pass
                else:
                    holder["status"] = httpx.post(
                        f"{BASE}/api/graphs/{graph_id}/run",
                        json={},
                        headers=client.headers,
                        timeout=700.0,
                    ).status_code
            except Exception as exc:  # noqa: BLE001 - 勘察脚本，任何异常都只记录
                holder["status"] = f"exc:{type(exc).__name__}"

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def runs() -> set[str]:
        return {
            r.get("runId")
            for r in client.get("/api/runs?limit=30").json().get("items", [])
            if r.get("graphId") == graph_id and r.get("status") in ("running", "suspended")
        }

    # 3) 审批挂起：_pending 只在执行所在 worker 的 broker 里
    holder_a: dict[str, Any] = {}
    thread_a = blocked_run(holder_a)
    time.sleep(3.0)
    run_id_a = next(iter(runs()), None)

    pending = Counter(
        "seen" if r.json().get("items") else "empty"
        for r in (client.get("/api/approvals") for _ in range(SAMPLES))
    )
    results.append(
        report("待处理审批可见性（approval_broker._pending 进程内）",
               "挂起中的审批每一次 GET 都应在列表里", pending, ("empty",),
               conclusive=conclusive)
    )

    # 4) sync /run 的急停：单实例下也失败（register 只在流式路径），不是多实例成因
    if run_id_a:
        sync_cancel = Counter(
            str(r.status_code)
            for r in (client.post(f"/api/runs/{run_id_a}/cancel") for _ in range(SAMPLES))
        )
        report("对照：sync /run 的急停（cancellation_broker 从未在同步路径登记）",
               "在途 run 取消应 200；此项与 worker 数无关", sync_cancel, ("409",),
               cause="单实例即已存在，非多实例成因")
        print("        （main.py:2696 的 register 只在 run/stream 的 worker 线程里调用）")

    # 5) 审批决策路由：打到非执行 worker 时，真实存在的 token 返回 404
    tokens = client.get("/api/approvals").json().get("items", [])
    if tokens:
        approval_token = tokens[0]["token"]
        decisions = Counter(
            str(r.status_code)
            for r in (
                client.post(f"/api/approvals/{approval_token}/decision", json={"decision": "approved"})
                for _ in range(SAMPLES)
            )
        )
        results.append(
            report("审批决策路由（决策打到非执行 worker）",
                   "首决 200、重复 409，绝不应出现 404", decisions, ("404",),
                   conclusive=conclusive)
        )
    else:
        print("[未采到] 审批决策路由：没拿到 pending token\n")

    thread_a.join(timeout=5)

    # 6) 流式运行急停：句柄注册在执行 worker，其它 worker 报「运行已结束」
    holder_b: dict[str, Any] = {}
    thread_b = blocked_run(holder_b, stream=True)
    time.sleep(3.0)
    run_id_b = next(iter(runs() - ({run_id_a} if run_id_a else set())), None)
    if run_id_b:
        stream_cancel = Counter(
            str(r.status_code)
            for r in (client.post(f"/api/runs/{run_id_b}/cancel") for _ in range(SAMPLES))
        )
        results.append(
            report("流式运行急停（cancellation_broker 进程内）",
                   "在途 run 的取消应恒 200（幂等）；409＝打到了没有该句柄的 worker",
                   stream_cancel, ("409",), conclusive=conclusive)
        )
    else:
        print("[未采到] 流式运行急停：没找到在途 run\n")
    thread_b.join(timeout=3)

    # 7) 登录节流：失败计数每 worker 一份（上限 5 次/600s）
    throttle = Counter(
        str(r.status_code)
        for r in (
            client.post("/api/auth/login", json={"username": "recon-nobody", "password": "wrong"})
            for _ in range(12)
        )
    )
    results.append(
        report("登录失败节流（iam/throttle 进程内，上限 5 次/600s）",
               "第 6 次起应持续被锁（429），而非继续放行", throttle, ("401",),
               conclusive=conclusive)
    )

    print("\n=== 汇总 ===")
    print(f"分发自检：{'通过' if distributed else '未通过（结论只配记为未采到）'}")
    print(f"多实例专属分裂命中：{sum(results)}/{len(results)} 项")
    print(f"运行端收尾：sync A={holder_a.get('status')} stream B={holder_b.get('status')}")
    client.close()


if __name__ == "__main__":
    main()
