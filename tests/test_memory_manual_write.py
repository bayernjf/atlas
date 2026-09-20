# -*- coding: utf-8 -*-
"""docs/28 批 4 ⑩（D35 部分取回）：记忆手动新建/编辑（U192–U200）。

进程内 MemoryStore.update：白名单合并、source 强制 manual、id/created_at 不变、
content 变更重算 embedding（不变保留）、不存在 None、非法入参 MemoryValidationError。
REST：POST /api/memories（operate，201，source=manual）、PUT（operate，合并/空体 422/
404/他租户 404）；viewer 无 operate 403；DELETE 仍 admin only（回归锁定）。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.principals import Principal, Role
from atlas.memory.items import MemoryStore
from atlas.memory.models import (
    MemoryValidationError,
    merge_manual_update,
)

client = TestClient(app)

T1_ADMIN = Principal(
    tenant_id="t1", tenant_name="A", username="admin-a", display_name="管理员", role=Role.ADMIN
)


@pytest.fixture(autouse=True)
def _admin_session():
    login = client.post("/api/auth/login", json={"username": "admin-a", "password": "admin123"})
    assert login.status_code == 200
    client.headers["Authorization"] = f"Bearer {login.json()['token']}"
    yield
    client.post("/api/demo/reset")
    client.headers.pop("authorization", None)


def _bearer(username: str, password: str) -> dict[str, str]:
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['token']}"}


# ---- 进程内 update：U192–U195 ----


def test_u192_update_merges_whitelist_and_forces_manual_keeps_id_and_created_at():
    store = MemoryStore()
    created = store.remember(
        kind="fact", content="原始事实内容", confidence=0.5, source="tool",
        metadata={"src": "run"},
    )
    updated = store.update(
        created["id"], kind="preference", content="改写后的偏好内容", confidence=0.9,
        scope={"user_id": "u-1"}, metadata={"tag": "edited"},
    )
    assert updated is not None
    assert updated["id"] == created["id"]
    assert updated["created_at"] == created["created_at"]
    assert updated["kind"] == "preference"
    assert updated["content"] == "改写后的偏好内容"
    assert updated["confidence"] == 0.9
    assert updated["scope"] == {"user_id": "u-1"}
    assert updated["metadata"] == {"tag": "edited"}
    # source 无论原为何值，手动编辑后强制 manual
    assert updated["source"] == "manual"
    assert "embedding" not in updated


def test_u193_update_recomputes_embedding_only_when_content_changes():
    store = MemoryStore()
    created = store.remember(kind="fact", content="退款已到账八十八元")
    before = next(item for item in store._items if item["id"] == created["id"])["embedding"]
    # 仅改 confidence（content 不变，仅首尾空白差异视为不变）→ embedding 保留
    store.update(created["id"], confidence=0.3, content="  退款已到账八十八元 ")
    kept = next(item for item in store._items if item["id"] == created["id"])["embedding"]
    assert kept == before
    # content 改变 → embedding 重算，且新内容可被语义检索命中
    store.update(created["id"], content="用户偏好顺丰快递周末配送")
    recomputed = next(item for item in store._items if item["id"] == created["id"])["embedding"]
    assert recomputed != before  # content 变化 → embedding 已重算
    hits = store.recall("顺丰周末配送", top_k=1)
    assert hits and hits[0]["id"] == created["id"]


def test_u194_update_missing_returns_none_and_invalid_raises():
    store = MemoryStore()
    assert store.update("mem-404", content="x") is None
    created = store.remember(kind="fact", content="待非法编辑")
    with pytest.raises(MemoryValidationError):
        store.update(created["id"], kind="nope")
    with pytest.raises(MemoryValidationError):
        store.update(created["id"], confidence=1.5)
    with pytest.raises(MemoryValidationError):
        store.update(created["id"], content="   ")
    with pytest.raises(MemoryValidationError):
        store.update(created["id"], id="mem-999")  # 不可改字段
    with pytest.raises(MemoryValidationError):
        store.update(created["id"], created_at="2020-01-01")


def test_u195_merge_helper_content_changed_flag_and_partial_fields():
    old = {
        "id": "mem-1", "kind": "fact", "content": "事实", "scope": {},
        "confidence": 1.0, "source": "tool", "metadata": {}, "created_at": "t",
    }
    params, changed = merge_manual_update(old, {"confidence": 0.2})
    assert changed is False
    assert params["confidence"] == 0.2
    assert params["kind"] == "fact"  # 未传字段沿用旧值
    assert params["source"] == "manual"
    _, changed2 = merge_manual_update(old, {"content": "  事实 "})  # strip 后等价
    assert changed2 is False
    _, changed3 = merge_manual_update(old, {"content": "新事实"})
    assert changed3 is True


# ---- REST POST/PUT：U196–U200 ----


def test_u196_post_creates_manual_memory_operator_and_admin_201():
    operator = _bearer("operator-a", "operator123")
    resp = client.post(
        "/api/memories",
        headers=operator,
        json={"kind": "preference", "content": "手动新建的偏好", "confidence": 0.8,
              "scope": {"user_id": "u-7"}, "metadata": {"via": "ui"}},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["source"] == "manual"
    assert body["kind"] == "preference"
    assert body["confidence"] == 0.8
    assert body["scope"] == {"user_id": "u-7"}
    assert body["id"].startswith("mem-")
    assert "embedding" not in body
    # admin 同样可建
    admin_resp = client.post("/api/memories", json={"kind": "fact", "content": "管理员手动事实"})
    assert admin_resp.status_code == 201


def test_u197_post_invalid_body_422():
    assert client.post("/api/memories", json={"kind": "nope", "content": "x"}).status_code == 422
    assert client.post("/api/memories", json={"kind": "fact", "content": "   "}).status_code == 422
    assert client.post(
        "/api/memories", json={"kind": "fact", "content": "x", "confidence": 2}
    ).status_code == 422
    # 额外字段 / source 入参均被拒（extra forbid，source 不可由调用方指定）
    assert client.post(
        "/api/memories", json={"kind": "fact", "content": "x", "source": "tool"}
    ).status_code == 422
    assert client.post(
        "/api/memories", json={"kind": "fact", "content": "x", "id": "mem-1"}
    ).status_code == 422


def test_u198_put_merges_fields_and_reindexes():
    created = client.post(
        "/api/memories", json={"kind": "fact", "content": "PUT 前的事实", "confidence": 0.4}
    ).json()
    resp = client.put(
        f"/api/memories/{created['id']}",
        json={"content": "PUT 后的偏好顺丰配送", "kind": "preference", "confidence": 0.66},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == created["id"]
    assert body["created_at"] == created["created_at"]
    assert body["content"] == "PUT 后的偏好顺丰配送"
    assert body["kind"] == "preference"
    assert abs(body["confidence"] - 0.66) < 1e-9
    assert body["source"] == "manual"
    # 新内容进索引
    search = client.get("/api/memories/search", params={"q": "顺丰配送"}).json()["results"]
    assert search and search[0]["id"] == created["id"]


def test_u199_put_empty_body_422_missing_404_cross_tenant_404():
    created = client.post("/api/memories", json={"kind": "fact", "content": "跨租户编辑探测"}).json()
    assert client.put(f"/api/memories/{created['id']}", json={}).status_code == 422
    assert client.put("/api/memories/mem-404", json={"content": "x"}).status_code == 404
    # 他租户不可见 → 404
    admin_b = _bearer("admin-b", "admin123")
    assert client.put(
        f"/api/memories/{created['id']}", headers=admin_b, json={"content": "篡改"}
    ).status_code == 404
    # 非法字段值 422
    assert client.put(f"/api/memories/{created['id']}", json={"kind": "bad"}).status_code == 422


def test_u200_put_requires_operate_and_delete_still_admin_only():
    created = client.post("/api/memories", json={"kind": "fact", "content": "权限探测记忆"}).json()
    viewer = _bearer("viewer-a", "viewer123")
    assert client.post(
        "/api/memories", headers=viewer, json={"kind": "fact", "content": "v"}
    ).status_code == 403
    assert client.put(
        f"/api/memories/{created['id']}", headers=viewer, json={"content": "v2"}
    ).status_code == 403
    # operator 可编辑（operate）但不可删除（administer）
    operator = _bearer("operator-a", "operator123")
    assert client.put(
        f"/api/memories/{created['id']}", headers=operator, json={"confidence": 0.1}
    ).status_code == 200
    assert client.delete(f"/api/memories/{created['id']}", headers=operator).status_code == 403
    # admin 可删
    assert client.delete(f"/api/memories/{created['id']}").status_code == 200
    assert client.delete(f"/api/memories/{created['id']}").status_code == 404
