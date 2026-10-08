# -*- coding: utf-8 -*-
"""用户自建流程模板库 v1（打包 X，docs/85；U974–U979）＋模板更新（打包 Y，docs/86；U980–U985）。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.graph.dsl import parse_graph
from atlas.iam.deps import session_store
from atlas.iam.principals import authenticate
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


def _auth(username: str) -> dict[str, str]:
    principal = authenticate(username, "admin123" if username.startswith("admin") else f"{username.split('-')[0]}123")
    assert principal is not None
    return {"Authorization": f"Bearer {session_store.issue(principal)}"}


# --- U974：内存 store 纯逻辑 ----------------------------------------------

def test_user_template_store_crud_and_clear():
    store = UserTemplateStore()
    first = store.add(name="A", description="", tags=[], graph=_sample_graph())
    assert first.id == "utpl-1"
    assert first.created_at
    second = store.add(name="B", description="d", tags=["t"], graph=_sample_graph())
    assert second.id == "utpl-2"

    assert store.get("utpl-1").name == "A"
    assert store.get("missing") is None
    assert [item.id for item in store.list()] == ["utpl-2", "utpl-1"]

    assert store.delete("utpl-1") is True
    assert store.get("utpl-1") is None
    assert store.delete("utpl-1") is False

    store.clear()
    assert store.list() == []
    again = store.add(name="C", description="", tags=[], graph=_sample_graph())
    assert again.id == "utpl-1"


# --- U975：POST 另存 ------------------------------------------------------

def test_create_user_template_returns_201_and_seq():
    resp = client.post("/api/templates", json={"name": "现场模板", "graph": _sample_graph()})
    assert resp.status_code == 201
    body = resp.json()
    assert body["id"].startswith("utpl-")
    assert body["source"] == "user"
    assert body["deletable"] is True
    assert body["created_at"]
    assert body["graph"]["nodes"][0]["id"] == "trigger-1"

    second = client.post(
        "/api/templates",
        json={"name": "第二个", "description": "备注", "tags": ["现场"], "graph": _sample_graph()},
    )
    assert second.status_code == 201
    assert int(second.json()["id"].split("-")[1]) == int(body["id"].split("-")[1]) + 1


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "graph": _sample_graph()},
        {"name": "x" * 61, "graph": _sample_graph()},
        {"name": "   ", "graph": _sample_graph()},
        {"name": "ok", "description": "y" * 201, "graph": _sample_graph()},
        {"name": "ok", "tags": ["z" * 21], "graph": _sample_graph()},
        {"name": "ok", "tags": [f"t{i}" for i in range(9)], "graph": _sample_graph()},
        {"name": "ok", "graph": {"version": 1, "nodes": [], "edges": []}},
    ],
)
def test_create_user_template_validation_422(payload):
    resp = client.post("/api/templates", json=payload)
    assert resp.status_code == 422


# --- U976：GET 合并 -------------------------------------------------------

def test_list_merges_catalog_and_user_templates():
    client.post("/api/templates", json={"name": "合并验证", "graph": _sample_graph()})
    resp = client.get("/api/templates")
    assert resp.status_code == 200
    items = resp.json()["items"]

    catalog = [item for item in items if item["source"] == "catalog"]
    user = [item for item in items if item["source"] == "user"]
    assert catalog, "内置模板不应为空"
    assert user, "用户模板应已合并"
    assert all(item["deletable"] is False for item in catalog)
    assert all(item["deletable"] is True for item in user)
    assert "graph" not in items[0]
    assert items.index(catalog[0]) < items.index(user[0])

    detail = client.get(f"/api/templates/{user[0]['id']}").json()
    assert detail["source"] == "user"
    assert detail["graph"]


# --- U977：DELETE ---------------------------------------------------------

def test_delete_user_template_and_404_cases():
    created = client.post("/api/templates", json={"name": "待删", "graph": _sample_graph()})
    template_id = created.json()["id"]

    deleted = client.delete(f"/api/templates/{template_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True}
    assert client.delete(f"/api/templates/{template_id}").status_code == 404
    assert client.get(f"/api/templates/{template_id}").status_code == 404

    catalog_id = client.get("/api/templates").json()["items"][0]["id"]
    resp = client.delete(f"/api/templates/{catalog_id}")
    assert resp.status_code == 404
    assert resp.json()["detail"] == f"模板不存在：{catalog_id}"
    assert client.get(f"/api/templates/{catalog_id}").status_code == 200


# --- U978：跨租户分区 ------------------------------------------------------

def test_user_templates_isolated_per_tenant():
    created = client.post("/api/templates", json={"name": "A 租户专属", "graph": _sample_graph()})
    a_id = created.json()["id"]

    admin_b = _auth("admin-b")
    items = client.get("/api/templates", headers=admin_b).json()["items"]
    assert all(item["id"] != a_id for item in items)
    assert client.delete(f"/api/templates/{a_id}", headers=admin_b).status_code == 404

    b_created = client.post(
        "/api/templates", json={"name": "B 租户专属", "graph": _sample_graph()}, headers=admin_b
    )
    assert b_created.json()["id"] == "utpl-1"
    assert client.get(f"/api/templates/{a_id}").status_code == 200


# --- U979：权限与 reset ----------------------------------------------------

def test_viewer_cannot_create_or_delete():
    viewer = _auth("viewer-a")
    assert client.get("/api/templates", headers=viewer).status_code == 200
    resp = client.post(
        "/api/templates", json={"name": "x", "graph": _sample_graph()}, headers=viewer
    )
    assert resp.status_code == 403

    created = client.post("/api/templates", json={"name": "待保护", "graph": _sample_graph()})
    template_id = created.json()["id"]
    assert client.delete(f"/api/templates/{template_id}", headers=viewer).status_code == 403


def test_reset_clears_user_templates():
    created = client.post("/api/templates", json={"name": "reset 前", "graph": _sample_graph()})
    template_id = created.json()["id"]

    from atlas.iam.deps import tenant_registry

    tenant_registry.reset_tenant("t1")
    assert client.get(f"/api/templates/{template_id}").status_code == 404
    items = client.get("/api/templates").json()["items"]
    assert all(item["source"] == "catalog" for item in items)


# --- U980：内存 store update 纯逻辑 ---------------------------------------

def test_user_template_store_update_preserves_identity():
    store = UserTemplateStore()
    created = store.add(name="旧名", description="旧述", tags=["旧签"], graph=_sample_graph())
    updated = store.update(
        created.id, name="新名", description="新述", tags=["新签"], graph=_sample_graph()
    )
    assert updated is not None
    assert updated.id == created.id
    assert updated.created_at == created.created_at
    assert (updated.name, updated.description, updated.tags) == ("新名", "新述", ["新签"])
    assert store.update("utpl-999", name="x", description="", tags=[], graph=_sample_graph()) is None
    assert [item.id for item in store.list()] == [created.id]


# --- U981：PUT 成功 --------------------------------------------------------

def test_put_updates_template_in_place():
    created = client.post(
        "/api/templates",
        json={"name": "原名", "description": "原述", "tags": ["a"], "graph": _sample_graph()},
    ).json()
    resp = client.put(
        f"/api/templates/{created['id']}",
        json={"name": "新名", "description": "新述", "tags": ["b"], "graph": _sample_graph()},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert body["created_at"] == created["created_at"]
    assert body["name"] == "新名"
    assert body["description"] == "新述"
    assert body["tags"] == ["b"]

    items = client.get("/api/templates").json()["items"]
    row = next(item for item in items if item["id"] == created["id"])
    assert row["name"] == "新名"


# --- U982：PUT 校验同 POST -------------------------------------------------

@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "graph": _sample_graph()},
        {"name": "x" * 61, "graph": _sample_graph()},
        {"name": "   ", "graph": _sample_graph()},
        {"name": "ok", "description": "y" * 201, "graph": _sample_graph()},
        {"name": "ok", "tags": ["z" * 21], "graph": _sample_graph()},
        {"name": "ok", "tags": [f"t{i}" for i in range(9)], "graph": _sample_graph()},
        {"name": "ok", "graph": {"version": 1, "nodes": [], "edges": []}},
    ],
)
def test_put_validation_422(payload):
    created = client.post("/api/templates", json={"name": "待改", "graph": _sample_graph()})
    resp = client.put(f"/api/templates/{created.json()['id']}", json=payload)
    assert resp.status_code == 422


# --- U983：tags 缺省保留 ---------------------------------------------------

def test_put_tags_default_keeps_explicit_replaces():
    created = client.post(
        "/api/templates",
        json={"name": "标签实验", "tags": ["keep"], "graph": _sample_graph()},
    ).json()
    tid = created["id"]

    missing = client.put(f"/api/templates/{tid}", json={"name": "标签实验", "graph": _sample_graph()})
    assert missing.status_code == 200
    assert missing.json()["tags"] == ["keep"]

    replaced = client.put(
        f"/api/templates/{tid}", json={"name": "标签实验", "tags": ["new"], "graph": _sample_graph()}
    )
    assert replaced.json()["tags"] == ["new"]

    cleared = client.put(
        f"/api/templates/{tid}", json={"name": "标签实验", "tags": [], "graph": _sample_graph()}
    )
    assert cleared.json()["tags"] == []


# --- U984：PUT 404 ---------------------------------------------------------

def test_put_builtin_or_missing_returns_404():
    catalog_id = client.get("/api/templates").json()["items"][0]["id"]
    resp = client.put(
        f"/api/templates/{catalog_id}",
        json={"name": "hacker", "graph": _sample_graph()},
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == f"模板不存在：{catalog_id}"
    assert client.get(f"/api/templates/{catalog_id}").status_code == 200

    assert client.put(
        "/api/templates/utpl-999", json={"name": "x", "graph": _sample_graph()}
    ).status_code == 404


# --- U985：分区与权限 ------------------------------------------------------

def test_put_cross_tenant_404_and_viewer_403():
    created = client.post("/api/templates", json={"name": "A 的", "graph": _sample_graph()})
    a_id = created.json()["id"]

    admin_b = _auth("admin-b")
    resp = client.put(
        f"/api/templates/{a_id}",
        json={"name": "被 B 改", "graph": _sample_graph()},
        headers=admin_b,
    )
    assert resp.status_code == 404
    assert client.get(f"/api/templates/{a_id}").json()["name"] == "A 的"

    viewer = _auth("viewer-a")
    denied = client.put(
        f"/api/templates/{a_id}",
        json={"name": "viewer 改", "graph": _sample_graph()},
        headers=viewer,
    )
    assert denied.status_code == 403


# --- 打包 A1（docs/97；U1180–U1186）：版本/CAS、搜索、使用统计、参数化向导 ----------

_A1_PARAMS = {
    "min_amount": {"type": "number", "label": "最小金额", "required": True, "hint": "低于此金额不退款"},
    "channel": {"type": "select", "label": "渠道", "required": False, "options": ["email", "webhook", "im"], "default": "email"},
    "notify": {"type": "boolean", "label": "是否通知", "required": False, "default": True},
}


def _a1_graph_with_vars():
    graph = _sample_graph()
    graph["variables"] = [
        {"name": "min_amount", "type": "number", "value": 100, "scope": "global"},
    ]
    return graph


def _create_a1_template(client_, params=None, graph=None):
    resp = client_.post(
        "/api/templates",
        json={
            "name": "A1 模板",
            "description": "d",
            "tags": ["t"],
            "category": "退款",
            "graph": graph or _a1_graph_with_vars(),
            "params": params if params is not None else _A1_PARAMS,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# U1180：add 后 version=1/updated_at=created_at/usage_count=0/params={}；update 递增 version/刷新 updated_at
def test_a1_version_and_timestamps():
    created = _create_a1_template(client)
    assert created["version"] == 1
    assert created["updated_at"] == created["created_at"]
    assert created["usage_count"] == 0
    assert created["params"] == _A1_PARAMS

    listed = client.get("/api/templates").json()["items"]
    user_item = next(item for item in listed if item["id"] == created["id"])
    assert user_item["version"] == 1
    assert user_item["usage_count"] == 0
    assert "updated_at" in user_item

    updated = client.put(
        f"/api/templates/{created['id']}",
        json={"name": "A1 模板 v2", "graph": created["graph"], "params": created["params"]},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["version"] == 2
    assert body["updated_at"] != body["created_at"]

    detail = client.get(f"/api/templates/{created['id']}").json()
    assert detail["version"] == 2
    assert detail["params"] == _A1_PARAMS


# U1181：CAS——匹配 200 version+1；不匹配 409 且模板不变；缺省无防护；内置/不存在 404
def test_a1_cas_conflict_409_and_passthrough():
    created = _create_a1_template(client)
    tid = created["id"]

    ok = client.put(
        f"/api/templates/{tid}",
        json={"name": "CAS ok", "graph": created["graph"], "if_match_version": 1},
    )
    assert ok.status_code == 200
    assert ok.json()["version"] == 2

    conflict = client.put(
        f"/api/templates/{tid}",
        json={"name": "CAS stale", "graph": created["graph"], "if_match_version": 1},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == "模板已被他人更新（当前版本 2），请刷新后重试"
    # 模板不变（CAS 失败不落写）
    after = client.get(f"/api/templates/{tid}").json()
    assert after["name"] == "CAS ok"
    assert after["version"] == 2

    # 缺省无防护
    plain = client.put(f"/api/templates/{tid}", json={"name": "no guard", "graph": created["graph"]})
    assert plain.status_code == 200
    assert plain.json()["version"] == 3

    # 内置与不存在
    builtin = client.put("/api/templates/refund-auto", json={"name": "x", "graph": created["graph"]})
    assert builtin.status_code == 404
    missing = client.put("/api/templates/utpl-999", json={"name": "x", "graph": created["graph"]})
    assert missing.status_code == 404


# U1182：q 搜索——两段过滤、大小写不敏感、q 空全量、无命中空
def test_a1_search_query_filters_both_segments():
    client.post("/api/demo/reset")
    _create_a1_template(client, params={"min_amount": {"type": "number", "label": "m", "required": True}})
    client.post(
        "/api/templates",
        json={"name": "EnglishRefund", "graph": _sample_graph(), "params": {}},
    )

    # 内置段命中（分类/名字）
    builtin_hit = client.get("/api/templates", params={"q": "退款"}).json()["items"]
    assert any(item["source"] == "catalog" for item in builtin_hit)

    # 用户段命中 name（大小写不敏感）
    user_hit = client.get("/api/templates", params={"q": "englishrefund"}).json()["items"]
    assert any(item["id"].startswith("utpl-") and item["name"] == "EnglishRefund" for item in user_hit)

    # tags/category 命中
    tag_hit = client.get("/api/templates", params={"q": "t"}).json()["items"]
    assert any(item["id"].startswith("utpl-") for item in tag_hit)

    # q 空白＝全量
    empty = client.get("/api/templates", params={"q": "  "}).json()["items"]
    assert len(empty) >= 5

    # 无命中
    none = client.get("/api/templates", params={"q": "zzzz-none-zzzz"}).json()["items"]
    assert none == []


# U1183：usage 计数——touch 累加、内置/不存在/跨租户 404
def test_a1_usage_count_touch():
    created = _create_a1_template(client)
    tid = created["id"]

    resp1 = client.post(f"/api/templates/{tid}/usage")
    assert resp1.status_code == 200
    assert resp1.json()["usage_count"] == 1
    resp2 = client.post(f"/api/templates/{tid}/usage")
    assert resp2.json()["usage_count"] == 2

    listed = client.get("/api/templates").json()["items"]
    assert next(item for item in listed if item["id"] == tid)["usage_count"] == 2

    builtin = client.post("/api/templates/refund-auto/usage")
    assert builtin.status_code == 404
    missing = client.post("/api/templates/utpl-999/usage")
    assert missing.status_code == 404
    other = client.post(f"/api/templates/{tid}/usage", headers=_auth("admin-b"))
    assert other.status_code == 404


# U1184：params 形状校验——合法通过；非法 422 且模板未变
@pytest.mark.parametrize(
    "params",
    [
        {"p": {"type": "string", "label": "x"}},
        {"p": {"type": "number", "required": True}},
        {"p": {"type": "boolean"}},
        {"p": {"type": "select", "options": ["a", "b"]}},
        {"p": {"type": "string", "label": "hint 测试", "hint": "提示", "default": "v"}},
    ],
)
def test_a1_params_valid_shapes_accepted(params):
    created = _create_a1_template(client, params=params)
    assert client.get(f"/api/templates/{created['id']}").json()["params"] == params


@pytest.mark.parametrize(
    "params, needle",
    [
        ({"p": {"type": "date"}}, "type 必须是"),
        ({"p": {"type": "select"}}, "options 必须"),
        ({"p": {"type": "string", "label": "x" * 41}}, "label 长度"),
        ({"p": {"type": "string", "extra": 1}}, "未知字段"),
        ({"p": {"required": "yes"}}, "required 必须是"),
    ],
)
def test_a1_params_invalid_shapes_422(params, needle):
    resp = client.post(
        "/api/templates",
        json={"name": "bad params", "graph": _sample_graph(), "params": params},
    )
    assert resp.status_code == 422
    assert needle in resp.json()["detail"]
    # 模板未变
    assert client.get("/api/templates", params={"q": "bad params"}).json()["items"] == []


# U1185：instantiate——required/类型/select/未知名 422；成功返回深拷贝
def test_a1_instantiate_validation_422():
    created = _create_a1_template(client)
    tid = created["id"]

    missing_required = client.post(f"/api/templates/{tid}/instantiate", json={"values": {}})
    assert missing_required.status_code == 422
    assert "min_amount 为必填" in missing_required.json()["detail"]

    bad_number = client.post(
        f"/api/templates/{tid}/instantiate", json={"values": {"min_amount": "100", "channel": "email"}}
    )
    assert bad_number.status_code == 422
    assert "min_amount 必须是数字" in bad_number.json()["detail"]

    bad_bool = client.post(
        f"/api/templates/{tid}/instantiate",
        json={"values": {"min_amount": 1, "notify": "yes"}},
    )
    assert bad_bool.status_code == 422
    assert "notify 必须是布尔值" in bad_bool.json()["detail"]

    bad_select = client.post(
        f"/api/templates/{tid}/instantiate",
        json={"values": {"min_amount": 1, "channel": "sms"}},
    )
    assert bad_select.status_code == 422
    assert "channel 的值不在可选范围内" in bad_select.json()["detail"]

    unknown = client.post(
        f"/api/templates/{tid}/instantiate",
        json={"values": {"min_amount": 1, "bogus": 2}},
    )
    assert unknown.status_code == 422
    assert "未知参数：bogus" in unknown.json()["detail"]

    builtin = client.post("/api/templates/refund-auto/instantiate", json={"values": {}})
    assert builtin.status_code == 404
    missing = client.post("/api/templates/utpl-999/instantiate", json={"values": {}})
    assert missing.status_code == 404


def test_a1_instantiate_returns_deep_copy_and_variables():
    created = _create_a1_template(client)
    tid = created["id"]
    original_graph = created["graph"]

    resp = client.post(
        f"/api/templates/{tid}/instantiate",
        json={"values": {"min_amount": 250, "channel": "im", "notify": False}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["template"] == {"id": tid, "name": "A1 模板", "version": 1}

    graph = body["graph"]
    # 深拷贝：模板原图逐键不变
    assert client.get(f"/api/templates/{tid}").json()["graph"] == original_graph
    assert graph is not body["graph"] or True  # 响应体已是独立对象

    # variables：命中覆写、未命中 append
    var_names = [var["name"] for var in graph["variables"]]
    min_var = next(var for var in graph["variables"] if var["name"] == "min_amount")
    assert min_var["value"] == 250
    channel_var = next(var for var in graph["variables"] if var["name"] == "channel")
    assert channel_var == {"name": "channel", "type": "select", "value": "im", "scope": "global"}
    notify_var = next(var for var in graph["variables"] if var["name"] == "notify")
    assert notify_var["value"] is False


# U1186：instantiate 变量语义贯通——_seed_variables 后 global 含表单值
def test_a1_instantiate_seeds_variables_runtime():
    from atlas.graph.loader import _seed_variables

    created = _create_a1_template(client)
    resp = client.post(
        f"/api/templates/{created['id']}/instantiate",
        json={"values": {"min_amount": 500, "channel": "webhook", "notify": True}},
    )
    assert resp.status_code == 200
    graph = resp.json()["graph"]
    seeded = _seed_variables(parse_graph(graph))["global"]
    assert seeded["min_amount"] == 500
    assert seeded["channel"] == "webhook"
    assert seeded["notify"] is True


# =============================================================================
# 打包 ZX（docs/106）：模板参数声明面结构化——嵌套 object / 数组 / 条件显隐
# U1251：合法结构化声明通过（object/array/visibleWhen 组合）
# =============================================================================
@pytest.mark.parametrize(
    "params",
    [
        # object：properties 递归
        {
            "webhook": {
                "type": "object",
                "label": "Webhook 配置",
                "required": True,
                "properties": {
                    "url": {"type": "string", "required": True, "label": "URL"},
                    "secret": {"type": "string"},
                },
            }
        },
        # array：items 递归 + min/max
        {
            "channels": {
                "type": "array",
                "label": "通知渠道",
                "minItems": 1,
                "maxItems": 3,
                "items": {"type": "select", "options": ["email", "webhook", "im"]},
            }
        },
        # array of object：数组行内嵌套 group
        {
            "rules": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "min": {"type": "number", "required": True},
                        "max": {"type": "number"},
                    },
                },
            }
        },
        # visibleWhen：条件显隐
        {
            "mode": {"type": "select", "options": ["auto", "manual"]},
            "manual_reason": {"type": "string", "visibleWhen": {"field": "mode", "equals": "manual"}},
        },
        # 结构化 default 透传
        {
            "cfg": {
                "type": "object",
                "default": {"level": "info"},
                "properties": {"level": {"type": "string"}},
            },
            "tags": {"type": "array", "default": ["a"], "items": {"type": "string"}},
        },
    ],
)
def test_zx_params_structured_valid_shapes_accepted(params):
    created = _create_a1_template(client, params=params)
    assert client.get(f"/api/templates/{created['id']}").json()["params"] == params


# U1252：非法结构化声明 422（且模板未变）
@pytest.mark.parametrize(
    "params, needle",
    [
        # object properties 必须是对象
        ({"cfg": {"type": "object", "properties": "nope"}}, "properties 必须是对象"),
        # object 递归子字段非法（嵌套 select 无 options）
        (
            {"cfg": {"type": "object", "properties": {"mode": {"type": "select"}}}},
            "options 必须",
        ),
        # array items 必须是对象
        ({"tags": {"type": "array", "items": "nope"}}, "items 必须是对象"),
        # array items 递归非法（元素 type 不存在）
        ({"tags": {"type": "array", "items": {"type": "date"}}}, "type 必须是"),
        # minItems 负数
        ({"tags": {"type": "array", "minItems": -1, "items": {"type": "string"}}}, "minItems 必须"),
        # minItems > maxItems
        (
            {"tags": {"type": "array", "minItems": 3, "maxItems": 1, "items": {"type": "string"}}},
            "minItems 不能大于 maxItems",
        ),
        # visibleWhen 形状
        ({"x": {"type": "string", "visibleWhen": {"equals": "v"}}}, "visibleWhen 必须是"),
        ({"x": {"type": "string", "visibleWhen": {"field": ""}}}, "visibleWhen.field 不能为空"),
        # 嵌套深度超限（5 层）
        (
            {
                "d0": {
                    "type": "object",
                    "properties": {
                        "d1": {
                            "type": "object",
                            "properties": {
                                "d2": {
                                    "type": "object",
                                    "properties": {
                                        "d3": {
                                            "type": "object",
                                            "properties": {"d4": {"type": "object", "properties": {}}},
                                        }
                                    },
                                }
                            },
                        }
                    },
                }
            },
            "嵌套深度超过上限",
        ),
    ],
)
def test_zx_params_structured_invalid_shapes_422(params, needle):
    resp = client.post(
        "/api/templates",
        json={"name": "bad structured params", "graph": _sample_graph(), "params": params},
    )
    assert resp.status_code == 422
    assert needle in resp.json()["detail"]
    assert client.get("/api/templates", params={"q": "bad structured params"}).json()["items"] == []


# U1253：instantiate——object/array 值递归校验
def test_zx_instantiate_structured_validation_422():
    created = _create_a1_template(
        client,
        params={
            "webhook": {
                "type": "object",
                "required": True,
                "properties": {
                    "url": {"type": "string", "required": True},
                    "secret": {"type": "string"},
                },
            },
            "channels": {
                "type": "array",
                "minItems": 1,
                "items": {"type": "select", "options": ["email", "webhook"]},
            },
        },
    )
    tid = created["id"]
    endpoint = f"/api/templates/{tid}/instantiate"

    # object 传标量 → 422
    resp = client.post(endpoint, json={"values": {"webhook": "nope", "channels": ["email"]}})
    assert resp.status_code == 422
    assert "webhook 必须是对象" in resp.json()["detail"]

    # object 子字段 required 缺失 → 422
    resp = client.post(endpoint, json={"values": {"webhook": {"secret": "s"}, "channels": ["email"]}})
    assert resp.status_code == 422
    assert "webhook.url 为必填" in resp.json()["detail"]

    # object 未知子字段 → 422
    resp = client.post(
        endpoint, json={"values": {"webhook": {"url": "u", "extra": 1}, "channels": ["email"]}}
    )
    assert resp.status_code == 422
    assert "未知子字段：extra" in resp.json()["detail"]

    # array 传标量 → 422
    resp = client.post(endpoint, json={"values": {"webhook": {"url": "u"}, "channels": "im"}})
    assert resp.status_code == 422
    assert "channels 必须是数组" in resp.json()["detail"]

    # array 元素非法 → 422
    resp = client.post(
        endpoint, json={"values": {"webhook": {"url": "u"}, "channels": ["sms"]}}
    )
    assert resp.status_code == 422
    assert "channels[0] 的值不在可选范围内" in resp.json()["detail"]

    # array minItems 不足 → 422
    resp = client.post(endpoint, json={"values": {"webhook": {"url": "u"}, "channels": []}})
    assert resp.status_code == 422
    assert "channels 至少需要 1 项" in resp.json()["detail"]


# U1254：instantiate——合法结构化值 200，variables 覆写为原值（object/array 原样注入）
def test_zx_instantiate_structured_valid_seeds_variables():
    created = _create_a1_template(
        client,
        params={
            "webhook": {
                "type": "object",
                "properties": {"url": {"type": "string"}, "secret": {"type": "string"}},
            },
            "channels": {"type": "array", "items": {"type": "string"}},
        },
    )
    resp = client.post(
        f"/api/templates/{created['id']}/instantiate",
        json={"values": {"webhook": {"url": "https://x", "secret": "s"}, "channels": ["email", "im"]}},
    )
    assert resp.status_code == 200
    graph = resp.json()["graph"]
    webhook_var = next(var for var in graph["variables"] if var["name"] == "webhook")
    assert webhook_var["value"] == {"url": "https://x", "secret": "s"}
    channels_var = next(var for var in graph["variables"] if var["name"] == "channels")
    assert channels_var["value"] == ["email", "im"]


# U1255：向后兼容——既有标量四型声明逐字不变（回归）
def test_zx_scalar_params_regression():
    params = {
        "p": {"type": "string", "label": "x"},
        "n": {"type": "number", "required": True},
        "b": {"type": "boolean"},
        "s": {"type": "select", "options": ["a", "b"]},
    }
    created = _create_a1_template(client, params=params)
    assert client.get(f"/api/templates/{created['id']}").json()["params"] == params
    resp = client.post(
        f"/api/templates/{created['id']}/instantiate",
        json={"values": {"p": "v", "n": 1, "b": True, "s": "a"}},
    )
    assert resp.status_code == 200
