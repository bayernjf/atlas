#!/usr/bin/env python3
"""A-08 知识库 20 文档检索评估（本地、确定性 embedding，不调真实模型）。

通过真实 HTTP（TestClient）登录、导入 20 篇 Markdown，再逐篇用唯一查询检索；
输出 top-1/top-3 命中与每条 citation，证据写 outputs/a08-knowledge-rag/20-doc-eval.json。
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from atlas.api.main import app

OUT = Path("outputs/a08-knowledge-rag/20-doc-eval.json")
ADMIN = {"username": "admin-a", "password": "admin123"}


def main() -> int:
    client = TestClient(app)
    login = client.post("/api/auth/login", json=ADMIN)
    if login.status_code != 200:
        print(f"login failed: {login.status_code} {login.text}", file=sys.stderr)
        return 1
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    client.post("/api/demo/reset")

    docs: list[dict] = []
    for index in range(20):
        code = f"ZETA-{index:02d}"
        raw = (
            f"# 项目 {code} 资料\n\n"
            f"项目代号 {code}，本项目的唯一业务规则是「{code} 审批必须双人复核」。\n\n"
            "通用说明：所有项目都需要保留审计记录。"
        ).encode("utf-8")
        resp = client.post(
            "/api/knowledge/import",
            json={
                "category": "rule",
                "filename": f"{code}.md",
                "content_base64": base64.b64encode(raw).decode("ascii"),
            },
        )
        if resp.status_code != 201:
            print(f"import {code} failed: {resp.status_code} {resp.text}", file=sys.stderr)
            return 1
        docs.append(
            {
                "code": code,
                "doc_id": hashlib.sha256(raw).hexdigest(),
                "imported": resp.json()["imported"],
            }
        )

    rows: list[dict] = []
    top1 = 0
    top3 = 0
    for doc in docs:
        query = f"{doc['code']} 审批双人复核"
        resp = client.get(
            "/api/memories/search",
            params={"q": query, "kind": "knowledge", "top_k": 3},
        )
        if resp.status_code != 200:
            print(f"search {doc['code']} failed: {resp.status_code} {resp.text}", file=sys.stderr)
            return 1
        results = resp.json()["results"]
        citations = [item.get("citation", {}) for item in results]
        ranks = [
            index + 1
            for index, citation in enumerate(citations)
            if citation.get("doc_id") == doc["doc_id"]
        ]
        rank = ranks[0] if ranks else None
        if rank == 1:
            top1 += 1
        if rank is not None:
            top3 += 1
        rows.append(
            {
                "code": doc["code"],
                "query": query,
                "expected_doc_id": doc["doc_id"],
                "rank": rank,
                "top3": citations,
            }
        )

    report = {
        "format": "A-08 knowledge retrieval eval",
        "embedding": "LocalDeterministicEmbedder (offline)",
        "documents": len(docs),
        "queries": len(rows),
        "top1_hits": top1,
        "top3_hits": top3,
        "top1_rate": round(top1 / len(rows), 4),
        "top3_rate": round(top3 / len(rows), 4),
        "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"A-08 eval: docs={report['documents']} queries={report['queries']} "
        f"top1={top1} top3={top3} -> {OUT}"
    )
    client.post("/api/demo/reset")
    return 0 if top3 == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
