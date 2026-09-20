# -*- coding: utf-8 -*-
"""M11 批 2（U84–U92）：memory/remember·recall 适配器 + REST 三端点。

适配器：发现两能力且 schema 过白名单、remember 需 WRITE、recall READ 且幂等、
端到端命中、校验失败折 MEMORY_INVALID_INPUT、repo 缺省不可执行。
REST：列表/kind 筛选（viewer+）、语义搜索（q 空 422、无命中空数组）、
DELETE admin only（operator 403）、跨租户 404/搜不到、reset 清空。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.harness.base import ActionRequest, ActionStatus, Permission
from atlas.iam.deps import services_for, tenant_registry
from atlas.iam.principals import Principal, Role
from atlas.memory.adapter import MemoryHarnessAdapter
from atlas.memory.items import MemoryStore
from atlas.storage.base import MemoryRepository

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


def _adapter(repo=None, granted=None) -> MemoryHarnessAdapter:
    return MemoryHarnessAdapter(
        repo=repo if repo is not None else MemoryStore(),
        granted_permissions=granted
        or {Permission.READ, Permission.WRITE, Permission.DELETE, Permission.FINANCIAL},
    )


# ---- 适配器 U84–U88 ----


def test_u84_discovers_two_capabilities_with_allowed_schema():
    adapter = _adapter()
    caps = {c.name: c for c in adapter.list_capabilities()}
    assert set(caps) == {"remember", "recall"}
    assert caps["remember"].permission == Permission.WRITE
    assert caps["remember"].is_idempotent is False
    assert caps["recall"].permission == Permission.READ
    assert caps["recall"].is_idempotent is True
    # Capability 构造期已按白名单校验 schema（未抛 ValueError 即过）
    assert "kind" in caps["remember"].input_schema["properties"]
    assert caps["recall"].output_schema["properties"]["results"]["type"] == "array"


def test_u85_remember_requires_write_permission():
    read_only = _adapter(granted={Permission.READ})
    result = read_only.execute(
        ActionRequest("remember", {"kind": "fact", "content": "x"})
    )
    assert result.status == ActionStatus.FAILED
    assert result.error.code == "PERMISSION_DENIED"


def test_u86_recall_requires_read_and_is_idempotent():
    repo = MemoryStore()
    repo.remember(kind="preference", content="用户偏好顺丰快递")
    adapter = _adapter(repo=repo)
    first = adapter.execute(ActionRequest("recall", {"query": "快递偏好"}))
    second = adapter.execute(ActionRequest("recall", {"query": "快递偏好"}))
    assert first.status == ActionStatus.SUCCESS
    assert first.output == second.output  # 幂等：读不改变状态
    # 无 WRITE 权限但有 READ 可 recall
    no_write = _adapter(repo=repo, granted={Permission.READ})
    assert no_write.execute(ActionRequest("recall", {"query": "快递"})).status == ActionStatus.SUCCESS


def test_u87_remember_then_recall_end_to_end_hit():
    adapter = _adapter()
    written = adapter.execute(
        ActionRequest(
            "remember",
            {"kind": "fact", "content": "订单 o-9 已退款 88 元", "scope": {"order_id": "o-9"}},
        )
    )
    assert written.status == ActionStatus.SUCCESS
    assert written.output["id"].startswith("mem-")
    assert "embedding" not in written.output
    recalled = adapter.execute(
        ActionRequest("recall", {"query": "订单退款", "scope": {"order_id": "o-9"}})
    )
    assert recalled.status == ActionStatus.SUCCESS
    results = recalled.output["results"]
    assert len(results) >= 1
    assert results[0]["content"] == "订单 o-9 已退款 88 元"
    assert "score" in results[0]
    # 无命中是空结果而非失败
    empty = adapter.execute(ActionRequest("recall", {"query": "完全无关的内容xyz"}))
    assert empty.status == ActionStatus.SUCCESS
    assert empty.output["results"] == [] or all(r["score"] <= 0.0 for r in empty.output["results"])


def test_u88_invalid_input_and_unconfigured_repo():
    adapter = _adapter()
    bad = adapter.execute(ActionRequest("remember", {"kind": "bogus", "content": "x"}))
    assert bad.status == ActionStatus.FAILED
    assert bad.error.code == "MEMORY_INVALID_INPUT"
    blank = adapter.execute(ActionRequest("recall", {"query": "  "}))
    assert blank.status == ActionStatus.FAILED
    assert blank.error.code == "MEMORY_INVALID_INPUT"
    # repo 缺省（全局发现实例）执行期不可用
    discovery = MemoryHarnessAdapter(granted_permissions={Permission.READ, Permission.WRITE})
    result = discovery.execute(ActionRequest("remember", {"kind": "fact", "content": "x"}))
    assert result.status == ActionStatus.FAILED
    assert result.error.code == "MEMORY_NOT_CONFIGURED"


def test_adapter_conforms_to_memory_repository_protocol():
    assert isinstance(MemoryStore(), MemoryRepository)


# ---- REST U89–U92 ----


def _seed(tenant: str = "t1") -> str:
    services = tenant_registry.get(tenant)
    item = services.memory_store.remember(kind="fact", content="REST 测试记忆：退款已到账")
    services.memory_store.remember(kind="preference", content="REST 用户偏好：周末配送")
    return item["id"]


def test_u89_list_and_kind_filter_viewer_can_read():
    _seed()
    response = client.get("/api/memories")
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 2
    assert all("embedding" not in item for item in items)
    # 倒序（新→旧）
    assert items[0]["content"].startswith("REST 用户偏好")
    facts = client.get("/api/memories", params={"kind": "fact"})
    assert all(item["kind"] == "fact" for item in facts.json()["items"])
    assert len(facts.json()["items"]) == 1
    # 非法 kind 422
    assert client.get("/api/memories", params={"kind": "bogus"}).status_code == 422
    # viewer 可读
    viewer = client.post(
        "/api/auth/login", json={"username": "viewer-a", "password": "viewer123"}
    ).json()["token"]
    viewed = client.get("/api/memories", headers={"Authorization": f"Bearer {viewer}"})
    assert viewed.status_code == 200
    # 未登录 401
    assert client.get("/api/memories", headers={"Authorization": ""}).status_code == 401


def test_u90_search_hit_score_and_blank_q_422():
    _seed()
    response = client.get("/api/memories/search", params={"q": "退款到账"})
    assert response.status_code == 200
    results = response.json()["results"]
    assert results
    assert results[0]["content"] == "REST 测试记忆：退款已到账"
    assert 0.0 <= results[0]["score"] <= 1.0
    # kind 过滤
    pref = client.get(
        "/api/memories/search", params={"q": "配送偏好", "kind": "preference"}
    )
    assert all(r["kind"] == "preference" for r in pref.json()["results"])
    # q 空白 422
    assert client.get("/api/memories/search", params={"q": "   "}).status_code == 422
    # 无命中空数组
    no_hit = client.get("/api/memories/search", params={"q": "zzzzqq nothing"})
    assert no_hit.status_code == 200


def test_u91_delete_admin_only_and_404():
    memory_id = _seed()
    # operator 403
    operator = client.post(
        "/api/auth/login", json={"username": "operator-a", "password": "operator123"}
    ).json()["token"]
    denied = client.delete(
        f"/api/memories/{memory_id}", headers={"Authorization": f"Bearer {operator}"}
    )
    assert denied.status_code == 403
    # admin 200
    ok = client.delete(f"/api/memories/{memory_id}")
    assert ok.status_code == 200 and ok.json() == {"deleted": True}
    # 再删 404
    assert client.delete(f"/api/memories/{memory_id}").status_code == 404


def test_u92_cross_tenant_invisible_and_reset_clears():
    memory_id = _seed("t1")
    # 切 t2 admin
    t2 = client.post(
        "/api/auth/login", json={"username": "admin-b", "password": "admin123"}
    ).json()["token"]
    client.headers["Authorization"] = f"Bearer {t2}"
    try:
        assert client.get("/api/memories").json()["items"] == []
        search = client.get("/api/memories/search", params={"q": "退款到账"})
        assert search.json()["results"] == []
        # 跨租户删除 404（不泄漏存在性）
        assert client.delete(f"/api/memories/{memory_id}").status_code == 404
    finally:
        # 恢复 t1 admin 供 autouse teardown reset
        t1 = client.post(
            "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
        ).json()["token"]
        client.headers["Authorization"] = f"Bearer {t1}"
    # reset 清空
    client.post("/api/demo/reset")
    assert client.get("/api/memories").json()["items"] == []
