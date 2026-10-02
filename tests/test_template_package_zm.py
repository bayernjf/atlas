# -*- coding: utf-8 -*-
"""模板库产品化切片：分类管理＋URL 导入导出（打包 ZM，docs/08 打包 ZM 立项块；U1063–U1066）。"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import session_store
from atlas.iam.principals import authenticate
from atlas.template import list_templates
from atlas.template.user_store import UserTemplateStore

client = TestClient(app)


@pytest.fixture(autouse=True)
def _admin_a_session():
    principal = authenticate("admin-a", "admin123")
    assert principal is not None
    token = session_store.issue(principal)
    client.headers["Authorization"] = f"Bearer {token}"
    try:
        yield
    finally:
        client.headers.pop("authorization", None)
        session_store.revoke(token)


def _sample_graph():
    return {
        "version": 1,
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
            {"id": "tool_call-1", "type": "tool_call", "name": "工具",
             "config": {"tool": "web-playwright/click"}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool_call-1"}],
    }


# --- U1063：分类投影与创建/更新 ------------------------------------------

def test_catalog_templates_expose_default_categories():
    """内置 5 模板各带默认主题分类（按 docs/08 打包 ZM 立项块）。"""
    body = client.get("/api/templates").json()
    by_id = {item["id"]: item for item in body["items"]}
    assert by_id["refund-auto"]["category"] == "退款流程"
    assert by_id["http-orders-branch"]["category"] == "数据查询"
    assert by_id["sql-query-notify"]["category"] == "数据查询"
    assert by_id["sql-approval-write"]["category"] == "审批协作"
    assert by_id["approval-timeout-reject"]["category"] == "审批协作"
    # 反向：内置常量与列表投影逐键一致（防投影漂移）
    catalog = {t.id: t for t in list_templates()}
    for template_id, item in by_id.items():
        if template_id.startswith("utpl"):
            continue
        assert item["category"] == catalog[template_id].category


def test_user_template_category_roundtrip():
    """创建带 category 回显、缺省空串、更新缺省保留旧值（照 tags 先例）。"""
    created = client.post(
        "/api/templates",
        json={"name": "带分类", "description": "", "tags": [], "category": "退款流程", "graph": _sample_graph()},
    ).json()
    assert created["category"] == "退款流程"
    template_id = created["id"]

    # 列表投影含 category
    listed = client.get("/api/templates").json()["items"]
    row = next(item for item in listed if item["id"] == template_id)
    assert row["category"] == "退款流程"

    # 更新缺省（不传 category）→ 保留旧值
    updated = client.put(
        f"/api/templates/{template_id}",
        json={"name": "带分类", "description": "", "graph": _sample_graph()},
    ).json()
    assert updated["category"] == "退款流程"

    # 显式传新分类 → 覆盖；显式空串 → 清空
    assert client.put(
        f"/api/templates/{template_id}",
        json={"name": "带分类", "description": "", "category": "数据查询", "graph": _sample_graph()},
    ).json()["category"] == "数据查询"
    assert client.put(
        f"/api/templates/{template_id}",
        json={"name": "带分类", "description": "", "category": "", "graph": _sample_graph()},
    ).json()["category"] == ""


def test_user_template_category_validation():
    """分类长度超限保存期 422（反向门；请求体层由 pydantic max_length=30 拦截）。"""
    response = client.post(
        "/api/templates",
        json={"name": "坏分类", "description": "", "category": "超" * 31, "graph": _sample_graph()},
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    # pydantic 结构（list）或端点内校验（str）任一形态均可
    assert "30" in (detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False))


def test_memory_store_category_roundtrip():
    """内存 store add/update category 直接往返（纯逻辑，防 API 层遮蔽）。"""
    store = UserTemplateStore()
    first = store.add(name="A", description="", tags=[], category="退款流程", graph=_sample_graph())
    assert first.category == "退款流程"
    second = store.add(name="B", description="", tags=[], graph=_sample_graph())
    assert second.category == ""
    updated = store.update(first.id, name="A", description="", tags=[], category="数据查询", graph=_sample_graph())
    assert updated.category == "数据查询"
    # 缺省保留旧值由 API 层决定，store 层语义：显式传什么存什么
    kept = store.update(first.id, name="A", description="", tags=[], graph=_sample_graph())
    assert kept.category == ""


# --- U1064：导出包 --------------------------------------------------------

def test_export_catalog_and_user_template_package():
    """内置/用户模板均可导出 atlas-template-v1 包，attachment 文件名正确。"""
    catalog_resp = client.get("/api/templates/refund-auto/export")
    assert catalog_resp.status_code == 200
    assert catalog_resp.headers["content-disposition"] == 'attachment; filename="refund-auto.atlas-template.json"'
    package = catalog_resp.json()
    assert package["format"] == "atlas-template-v1"
    assert package["meta"]["name"] == "退款自动审批"
    assert package["meta"]["category"] == "退款流程"
    assert "nodes" in package["graph"]

    created = client.post(
        "/api/templates",
        json={"name": "导出我", "description": "d", "tags": ["t"], "category": "审批协作", "graph": _sample_graph()},
    ).json()
    user_resp = client.get(f"/api/templates/{created['id']}/export")
    assert user_resp.status_code == 200
    user_package = user_resp.json()
    assert user_package["format"] == "atlas-template-v1"
    assert user_package["meta"] == {"name": "导出我", "description": "d", "tags": ["t"], "category": "审批协作"}


def test_export_unknown_template_404():
    """未知模板 id 导出 404（反向门）。"""
    assert client.get("/api/templates/utpl-999999/export").status_code == 404


# --- U1065：包导入 --------------------------------------------------------

def test_import_package_creates_user_template():
    """atlas-template-v1 包导入 → 创建用户模板，列表可见、可导出回读。"""
    source = client.get("/api/templates/refund-auto/export").json()
    response = client.post("/api/templates/import", json={"package": source})
    assert response.status_code == 201
    created = response.json()
    assert created["source"] == "user"
    assert created["category"] == "退款流程"
    assert created["name"] == "退款自动审批"

    # 列表可见 + 导出回读逐键一致（导出→导入→导出闭环）
    listed = client.get("/api/templates").json()["items"]
    assert any(item["id"] == created["id"] for item in listed)
    reexport = client.get(f"/api/templates/{created['id']}/export").json()
    assert reexport["meta"]["name"] == source["meta"]["name"]
    assert reexport["meta"]["category"] == source["meta"]["category"]
    assert reexport["graph"] == source["graph"]


@pytest.mark.parametrize(
    "package, expect_fragment",
    [
        ({"format": "atlas-template-v1", "meta": {"name": ""}, "graph": _sample_graph()}, "模板名称不能为空"),
        ({"format": "atlas-template-v1", "meta": {}, "graph": _sample_graph()}, "模板名称不能为空"),
        ({"format": "atlas-template-v2", "meta": {"name": "x"}, "graph": _sample_graph()}, "不认识的模板包格式"),
        ({"format": "atlas-template-v1", "meta": {"name": "x", "tags": "不是列表"}, "graph": _sample_graph()}, "标签须为列表"),
        ({"format": "atlas-template-v1", "meta": {"name": "x", "tags": ["超" * 21]}, "graph": _sample_graph()}, "标签长度须在 1-20"),
        ({"format": "atlas-template-v1", "meta": {"name": "x", "category": "超" * 31}, "graph": _sample_graph()}, "分类长度须在 30 字符以内"),
        ({"format": "atlas-template-v1", "meta": {"name": "x"}}, "模板包缺少 graph"),
        ({"format": "atlas-template-v1", "meta": {"name": "x"}, "graph": {"version": 1, "nodes": [{"id": "bad", "name": "坏节点", "type": "nope"}]}}, "类型暂不支持"),
    ],
)
def test_import_package_validation(package, expect_fragment):
    """包导入校验面：name 必填/format/tags/category/graph 编译——全部保存期 422（反向门）。"""
    response = client.post("/api/templates/import", json={"package": package})
    assert response.status_code == 422
    detail = response.json()["detail"]
    text = detail if isinstance(detail, str) else " ".join(str(item) for item in detail)
    assert expect_fragment in text


def test_import_requires_exactly_one_source():
    """package 与 url 必须且只能提供其一（反向门）。"""
    both = client.post("/api/templates/import", json={"package": {}, "url": "https://example.com/t.json"})
    assert both.status_code == 422
    assert "必须且只能提供" in both.json()["detail"]
    neither = client.post("/api/templates/import", json={})
    assert neither.status_code == 422


# --- U1066：远程 URL 导入 -------------------------------------------------

def test_import_url_requires_https():
    """非 https 远程导入保存期 422（反向门）。"""
    response = client.post("/api/templates/import", json={"url": "http://example.com/t.json"})
    assert response.status_code == 422
    assert "仅支持 https" in response.json()["detail"]


def test_import_url_egress_blocks_private_networks(monkeypatch):
    """内网/环回 URL 被 EgressGuard 拒 → 422（真 egress 逻辑，反向门）。"""
    import src.atlas.api.main as main

    # 不 monkeypatch egress：真 EgressGuard 应拒 127.0.0.1（denylist 恒启用）
    response = client.post("/api/templates/import", json={"url": "https://127.0.0.1/t.json"})
    assert response.status_code == 422
    assert "远程模板拉取失败" in response.json()["detail"]


def test_import_url_success_via_mocked_http(monkeypatch):
    """https 远程 URL 放行路径：egress 放行＋httpx 假响应 → 201 创建（正向对照）。"""
    import httpx
    import src.atlas.api.main as main

    class _FakeResponse:
        status_code = 200
        content = json.dumps(
            {"format": "atlas-template-v1", "meta": {"name": "远程模板", "category": "数据查询"}, "graph": _sample_graph()}
        ).encode()

        def json(self):
            return json.loads(self.content)

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, timeout=None):
            return _FakeResponse()

    # 放行 egress（本用例不测 egress 判定，只测拉取+校验+落库路径）
    monkeypatch.setattr(main._template_import_egress, "check", lambda url: None)
    monkeypatch.setattr(main.httpx, "Client", _FakeClient)

    response = client.post("/api/templates/import", json={"url": "https://example.com/share/t.json"})
    assert response.status_code == 201
    created = response.json()
    assert created["name"] == "远程模板"
    assert created["category"] == "数据查询"
    assert created["source"] == "user"


def test_import_url_rejects_oversize_body(monkeypatch):
    """远程包超过 256KB 上限 → 422（反向门）。"""
    import src.atlas.api.main as main

    class _FakeResponse:
        status_code = 200
        content = b"x" * (main._TEMPLATE_IMPORT_MAX_BYTES + 1)

        def json(self):
            raise ValueError("not json")

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, timeout=None):
            return _FakeResponse()

    monkeypatch.setattr(main._template_import_egress, "check", lambda url: None)
    monkeypatch.setattr(main.httpx, "Client", _FakeClient)
    response = client.post("/api/templates/import", json={"url": "https://example.com/big.json"})
    assert response.status_code == 422
    assert "256KB" in response.json()["detail"]
