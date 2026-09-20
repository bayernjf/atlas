# -*- coding: utf-8 -*-
"""M11 长期记忆全链路冒烟（真实 :8000，docs/26 §8/§9.2）。

纯 HTTP 驱动，admin-a（t1）：
  reset → 建 trigger→memory/remember→memory/recall 小图并运行
  → remember 产出 mem-id、recall 命中刚写记忆且 score>0
  → GET /api/memories 列表含该条
  → GET /api/memories/search?q= 命中
  → t2（admin-b）搜不到、DELETE 跨租户 404
  → 本租户 admin DELETE 200 / 再删 404
  → reset 后清空。

用法：
  先起后端：.venv/bin/uvicorn atlas.api.main:app --port 8000
  再跑：    .venv/bin/python scripts/dev/m11_smoke.py
"""
from __future__ import annotations

import json
import sys

import httpx

BASE_API = "http://localhost:8000"


def ok(cond: bool, msg: str) -> None:
    print(f"  {'OK ' if cond else 'FAIL '} {msg}")
    if not cond:
        raise AssertionError(msg)


def memory_graph() -> dict:
    """trigger(webhook) → memory/remember → memory/recall（同 scope 子集）。"""
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/memory"}},
            {"id": "remember-1", "type": "tool_call", "name": "记住偏好",
             "config": {"tool": "memory/remember", "params": json.dumps({
                 "kind": "preference",
                 "content": "冒烟记忆：客户偏好顺丰配送、周末无人收件",
                 "scope": {"user_id": "u-smoke"},
             })}},
            {"id": "recall-1", "type": "tool_call", "name": "检索偏好",
             "config": {"tool": "memory/recall", "params": json.dumps({
                 "query": "客户的配送偏好",
                 "scope": {"user_id": "u-smoke"},
                 "top_k": 3,
             })}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "remember-1"},
            {"id": "e2", "source": "remember-1", "target": "recall-1"},
        ],
    }


def login(cli: httpx.Client, username: str, password: str) -> None:
    resp = cli.post("/api/auth/login", json={"username": username, "password": password})
    ok(resp.status_code == 200, f"{username} 登录 {resp.status_code}")
    cli.headers["Authorization"] = f"Bearer {resp.json()['token']}"


def main() -> int:
    with httpx.Client(base_url=BASE_API, timeout=30) as cli:
        login(cli, "admin-a", "admin123")
        cli.post("/api/demo/reset")

        print("=== 适配器发现：memory 两能力 ===")
        adapters = cli.get("/api/adapters").json()
        memory_caps = [
            item for item in adapters
            if item.get("id") == "memory" or item.get("adapter_id") == "memory"
        ]
        ok(len(memory_caps) >= 1, "/api/adapters 含 memory 适配器")

        print("=== 图内 remember → recall ===")
        graph = memory_graph()
        graph_id = cli.post("/api/graphs", json=graph).json()["id"]
        run = cli.post(f"/api/graphs/{graph_id}/run", json={"inputs": {}})
        ok(run.status_code == 200, f"运行 {run.status_code}")
        outputs = run.json()["outputs"]
        remembered = (outputs.get("remember-1") or {}).get("result")
        recalled = (outputs.get("recall-1") or {}).get("result")
        ok(isinstance(remembered, dict) and str(remembered.get("id", "")).startswith("mem-"),
           f"remember 产出 mem-id（得 {remembered!r}）")
        ok("embedding" not in (remembered or {}), "remember 输出不含 embedding")
        results = (recalled or {}).get("results", [])
        ok(isinstance(results, list) and len(results) >= 1, "recall 返回结果数组且命中")
        hit = results[0] if results else {}
        ok("顺丰" in hit.get("content", "") and hit.get("score", 0) > 0,
           f"recall 命中刚写记忆且 score>0（得 score={hit.get('score')}）")
        memory_id = remembered["id"]

        print("=== REST 列表 / 搜索 ===")
        listed = cli.get("/api/memories").json()["items"]
        ok(any(item["id"] == memory_id for item in listed), "GET /api/memories 含该条")
        ok(all("embedding" not in item for item in listed), "列表不含 embedding")
        pref_only = cli.get("/api/memories", params={"kind": "preference"}).json()["items"]
        ok(all(item["kind"] == "preference" for item in pref_only), "kind=preference 过滤")
        search = cli.get("/api/memories/search", params={"q": "配送偏好"}).json()["results"]
        ok(any(item["id"] == memory_id for item in search), "语义搜索命中")
        ok(cli.get("/api/memories/search", params={"q": "  "}).status_code == 422,
           "q 空白 422")

        print("=== 跨租户隔离（t2 admin-b）===")
        with httpx.Client(base_url=BASE_API, timeout=30) as other:
            login(other, "admin-b", "admin123")
            ok(other.get("/api/memories").json()["items"] == [], "t2 列表为空")
            ok(other.get("/api/memories/search", params={"q": "配送偏好"}).json()["results"] == [],
               "t2 搜索不到 t1 记忆")
            cross = other.delete(f"/api/memories/{memory_id}")
            ok(cross.status_code == 404, f"跨租户 DELETE 404（得 {cross.status_code}）")

        print("=== 本租户删除与 reset ===")
        deleted = cli.delete(f"/api/memories/{memory_id}")
        ok(deleted.status_code == 200 and deleted.json() == {"deleted": True}, "本租户 DELETE 200")
        ok(cli.delete(f"/api/memories/{memory_id}").status_code == 404, "再删 404")
        cli.post("/api/demo/reset")
        ok(cli.get("/api/memories").json()["items"] == [], "reset 后记忆清空")

    print("M11_SMOKE_ALL_PASS")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AssertionError, httpx.HTTPError, KeyError) as exc:
        print(f"SMOKE_FAILED: {exc}")
        sys.exit(1)
