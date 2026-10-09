# -*- coding: utf-8 -*-
"""docs/108 打包 AA：知识库 / RAG MVP 验收（U1260–U1266；U1259/U1265 的 PG 腿在
test_memory_pg_integration.py 的 AA describe 块）。

进程内档 + REST（TestClient）覆盖：import 分段规则（段落优先/超长硬切/空段跳过/
超 200 截断）、kind=knowledge 写读、recall category 过滤、REST create 放行 knowledge
＋category 白名单校验。知识条目与既有 fact/preference 互不影响。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.memory.items import MemoryStore

client = TestClient(app)

T1_ADMIN = {"username": "admin-a", "password": "admin123"}


@pytest.fixture(autouse=True)
def _admin_session():
    login = client.post("/api/auth/login", json=T1_ADMIN)
    assert login.status_code == 200
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    yield
    client.post("/api/demo/reset")
    client.headers.pop("authorization", None)


# ---- U1259：kind=knowledge 写读 + 既有 kind 不受影响（进程内档；PG 腿见集成文件） ----


def test_u1259_inmemory_knowledge_kind_writable_and_fact_preference_untouched():
    repo = MemoryStore()
    item = repo.remember(
        kind="knowledge", content="FAQ：如何重置密码", metadata={"category": "faq"}
    )
    assert item["kind"] == "knowledge"
    assert item["metadata"]["category"] == "faq"
    fact = repo.remember(kind="fact", content="既有事实不受影响")
    pref = repo.remember(kind="preference", content="偏好：默认简体中文")
    assert fact["kind"] == "fact" and pref["kind"] == "preference"
    assert repo.list(kind="knowledge")[0]["content"] == "FAQ：如何重置密码"
    assert len(repo.list(kind="fact")) == 1
    assert len(repo.list(kind="preference")) == 1
    assert len(repo.list(kind="knowledge")) == 1


# ---- U1260：import 纯文本分段 ----


def test_u1260_import_segments_paragraphs_and_sets_category_embedding():
    resp = client.post(
        "/api/knowledge/import",
        json={
            "category": "rule",
            "text": "退款规则：超 7 天不退。\n\n换货规则：仅限未拆封。",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["imported"] == 2
    assert body["truncated"] is False
    assert len(body["items"]) == 2
    for item in body["items"]:
        assert item["kind"] == "knowledge"
        assert item["metadata"]["category"] == "rule"
        assert "embedding" not in item  # 对外剔除内部向量
    listed = client.get("/api/memories", params={"kind": "knowledge"}).json()["items"]
    assert len(listed) == 2


# ---- U1261：超长硬切 / 空段跳过 / 空 text 422 / 非法 category 422 ----


def test_u1261_import_hard_cut_blank_skip_and_validation():
    long_para = "长" * 2500  # 超 1200 硬切为 3 段
    resp = client.post(
        "/api/knowledge/import",
        json={"category": "sop", "text": f"{long_para}\n\n\n\n\n尾段"},
    )
    assert resp.status_code == 201
    body = resp.json()
    # 2500 → 1200+1200+100 = 3 段，加尾段共 4；空段全跳过
    assert body["imported"] == 4
    assert all(len(item["content"]) <= 1200 for item in body["items"])
    assert client.post(
        "/api/knowledge/import", json={"category": "faq", "text": "   \n\n  "}
    ).status_code == 422  # 全部为空段 → 无任何条目可入 → 422（text 空语义）
    assert client.post(
        "/api/knowledge/import", json={"category": "bogus", "text": "x"}
    ).status_code == 422  # category 非白名单 → pydantic 422


# ---- U1262：超 200 条截断 ----


def test_u1262_import_truncates_over_200():
    text = "\n\n".join(f"FAQ 条目 {i}" for i in range(210))
    resp = client.post("/api/knowledge/import", json={"category": "faq", "text": text})
    assert resp.status_code == 201
    body = resp.json()
    assert body["imported"] == 200
    assert body["truncated"] is True
    assert len(body["items"]) == 200


# ---- U1263：recall kind=knowledge 向量排序命中（确定性） ----


def test_u1263_recall_knowledge_deterministic_ranking():
    client.post(
        "/api/knowledge/import",
        json={"category": "faq", "text": "密码重置需要验证邮箱。\n\n订单发货后 48 小时可查物流。"},
    )
    r1 = client.get(
        "/api/memories/search", params={"q": "忘记密码怎么办", "kind": "knowledge"}
    )
    assert r1.status_code == 200
    results = r1.json()["results"]
    assert results, "knowledge 检索应命中"
    assert results[0]["kind"] == "knowledge"
    # 确定性：同 query 两次结果一致
    r2 = client.get(
        "/api/memories/search", params={"q": "忘记密码怎么办", "kind": "knowledge"}
    )
    assert r2.json()["results"] == results


# ---- U1264：recall category 过滤 ----


def test_u1264_recall_category_filter():
    client.post(
        "/api/knowledge/import",
        json={
            "category": "rule",
            "text": "退款必须保留原始包装。",
        },
    )
    client.post(
        "/api/knowledge/import",
        json={
            "category": "faq",
            "text": "常见问题：退款时效多久。",
        },
    )
    only_faq = client.get(
        "/api/memories/search",
        params={"q": "退款", "kind": "knowledge", "category": "faq"},
    ).json()["results"]
    assert only_faq, "faq 过滤应命中"
    assert all(item["metadata"].get("category") == "faq" for item in only_faq)
    only_rule = client.get(
        "/api/memories/search",
        params={"q": "退款", "kind": "knowledge", "category": "rule"},
    ).json()["results"]
    assert all(item["metadata"].get("category") == "rule" for item in only_rule)
    # 非法 category → 422
    assert (
        client.get(
            "/api/memories/search",
            params={"q": "x", "kind": "knowledge", "category": "bogus"},
        ).status_code
        == 422
    )


# ---- U1266：REST create/update kind=knowledge 合法 201 / 非法 category 422 ----


def test_u1266_rest_create_knowledge_with_category_validation():
    created = client.post(
        "/api/memories",
        json={"kind": "knowledge", "content": "规则：售后 7 天无理由", "metadata": {"category": "rule"}},
    )
    assert created.status_code == 201
    assert created.json()["kind"] == "knowledge"
    assert created.json()["metadata"]["category"] == "rule"
    # update 也放行 knowledge
    updated = client.put(
        f"/api/memories/{created.json()['id']}",
        json={"metadata": {"category": "manual"}},
    )
    assert updated.status_code == 200
    assert updated.json()["metadata"]["category"] == "manual"
    # 非法 category → 422（remember 层 MemoryValidationError → HTTP 422）
    assert (
        client.post(
            "/api/memories",
            json={"kind": "knowledge", "content": "x", "metadata": {"category": "bogus"}},
        ).status_code
        == 422
    )
    # 既有 fact/preference 仍正常
    assert (
        client.post("/api/memories", json={"kind": "fact", "content": "事实"}).status_code
        == 201
    )
