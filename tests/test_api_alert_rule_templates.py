"""内置告警规则模板目录与只读端点测试（docs/59 F-1；13 U646–U651）。

覆盖：
- U646 目录 4 个内置模板、id 唯一 kebab、元数据齐备；
- U647 每个模板 config 都是过 validate_rules 的完整 RuleConfig；
- U648 GET 列表投影不含 config；
- U649 GET 详情含完整 config（四段内置规则齐备）；
- U650 未知 id 404 中文 detail；
- U651 鉴权：未登录 401、viewer 可读（read）、一键应用走 PUT rules 需 administer（viewer 403、admin 成功且阈值生效）。
"""

from __future__ import annotations

import re

from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.iam.deps import tenant_registry
from atlas.monitoring.alerts import validate_rules
from atlas.monitoring.rule_templates import RULE_TEMPLATES

client = TestClient(app)

EXPECTED_IDS = {
    "default-balanced",
    "strict-sre",
    "demo-lenient",
    "custom-quickstart",
}


def _headers(username: str = "admin-a", password: str = "admin123") -> dict:
    token = client.post(
        "/api/auth/login", json={"username": username, "password": password}
    ).json()["token"]
    return {"Authorization": f"Bearer {token}"}


def setup_function():
    tenant_registry.reset_tenant("t1")


# U646 ---------------------------------------------------------------------
def test_catalog_has_four_unique_kebab_templates_with_metadata():
    ids = [tpl.id for tpl in RULE_TEMPLATES]
    assert len(ids) == 4
    assert set(ids) == EXPECTED_IDS
    assert len(set(ids)) == len(ids)
    for tpl in RULE_TEMPLATES:
        assert re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", tpl.id)
        assert tpl.name.strip()
        assert tpl.description.strip()
        assert tpl.tags and all(tag.strip() for tag in tpl.tags)


# U647 ---------------------------------------------------------------------
def test_every_template_config_is_valid_complete_rule_config():
    required = {
        "run_error",
        "node_failed",
        "consecutive_failures",
        "failure_rate",
        "custom",
    }
    for tpl in RULE_TEMPLATES:
        assert required <= set(tpl.config), tpl.id
        assert validate_rules(tpl.config) == [], (tpl.id, validate_rules(tpl.config))


# U648 ---------------------------------------------------------------------
def test_list_endpoint_projects_without_config():
    resp = client.get("/api/alert-rule-templates", headers=_headers())
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert {item["id"] for item in items} == EXPECTED_IDS
    for item in items:
        assert "config" not in item
        assert item["name"] and item["description"] and isinstance(item["tags"], list)


# U649 ---------------------------------------------------------------------
def test_detail_endpoint_returns_full_config():
    resp = client.get(
        "/api/alert-rule-templates/strict-sre", headers=_headers()
    )
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["id"] == "strict-sre"
    config = payload["config"]
    assert config["consecutive_failures"]["threshold"] == 2
    assert config["failure_rate"]["rate"] == 0.3
    assert config["escalation_ack_minutes"] == 15
    # custom-quickstart 详情带两条自定义表达式
    cq = client.get(
        "/api/alert-rule-templates/custom-quickstart", headers=_headers()
    ).json()
    assert len(cq["config"]["custom"]) == 2


# U650 ---------------------------------------------------------------------
def test_unknown_template_returns_chinese_404():
    resp = client.get("/api/alert-rule-templates/nope", headers=_headers())
    assert resp.status_code == 404
    assert "nope" in resp.json()["detail"]


# U651 ---------------------------------------------------------------------
def test_auth_and_one_click_apply_via_put_rules():
    client.cookies.clear()  # 打包 ZQ Q4：httpOnly Cookie 由前序测试残留——未认证用例需干净 jar

    # 未登录 401
    assert client.get("/api/alert-rule-templates").status_code == 401
    # viewer 可读（read）
    viewer = _headers("viewer-a", "viewer123")
    assert client.get("/api/alert-rule-templates", headers=viewer).status_code == 200
    assert (
        client.get("/api/alert-rule-templates/strict-sre", headers=viewer).status_code
        == 200
    )
    # viewer 不能一键应用（PUT rules 为 administer/admin only）
    strict = client.get(
        "/api/alert-rule-templates/strict-sre", headers=viewer
    ).json()["config"]
    denied = client.put("/api/monitoring/rules", json=strict, headers=viewer)
    assert denied.status_code == 403
    # admin 一键应用：取 config 全量替换，随后 GET rules 反映模板阈值
    admin = _headers()
    ok = client.put("/api/monitoring/rules", json=strict, headers=admin)
    assert ok.status_code == 200
    applied = client.get("/api/monitoring/rules", headers=admin).json()
    assert applied["consecutive_failures"]["threshold"] == 2
    assert applied["failure_rate"]["rate"] == 0.3
    assert applied["escalation_ack_minutes"] == 15


# 打包 ZS（docs/102；13 U1220–U1226）：用户自建规则模板 CRUD + 列表/详情合并 source

VALID_CONFIG = {
    "run_error": {"enabled": True},
    "node_failed": {"enabled": True},
    "consecutive_failures": {"enabled": True, "threshold": 3},
    "failure_rate": {"enabled": True, "window": 20, "min_samples": 5, "rate": 0.5},
    "custom": [],
    "escalation_ack_minutes": None,
    "recovery_healthy_streak": 1,
    "recovery_cooldown_minutes": None,
}


def _create(name: str, admin: dict | None = None, **overrides) -> dict:
    admin = admin or _headers()
    payload = {"name": name, "description": "d", "tags": ["ops"], "config": VALID_CONFIG}
    payload.update(overrides)
    return client.post("/api/alert-rule-templates", json=payload, headers=admin)


# U1220 ---------------------------------------------------------------------
def test_user_template_appears_in_merged_list_with_source():
    admin = _headers()
    created = _create("my-rule", admin)
    assert created.status_code == 200
    assert created.json()["id"] == "urt-1"
    assert created.json()["source"] == "user"

    resp = client.get("/api/alert-rule-templates", headers=admin)
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert items[0]["id"] == "urt-1" and items[0]["source"] == "user"
    assert {item["id"] for item in items} == EXPECTED_IDS | {"urt-1"}
    for item in items:
        assert "config" not in item
        assert item["source"] in ("builtin", "user")
    assert {item["source"] for item in items} == {"builtin", "user"}


# U1221 ---------------------------------------------------------------------
def test_detail_merges_user_and_builtin_with_source():
    admin = _headers()
    assert _create("detail-rule", admin).status_code == 200
    user_detail = client.get("/api/alert-rule-templates/urt-1", headers=admin).json()
    assert user_detail["source"] == "user"
    assert user_detail["config"] == VALID_CONFIG
    assert user_detail["id"] == "urt-1" and user_detail["created_at"]
    builtin_detail = client.get(
        "/api/alert-rule-templates/strict-sre", headers=admin
    ).json()
    assert builtin_detail["source"] == "builtin"
    assert builtin_detail["config"]["failure_rate"]["rate"] == 0.3


# U1222 ---------------------------------------------------------------------
def test_create_validation_and_name_conflict():
    admin = _headers()
    # 坏 config → 422（RULE_TEMPLATE_CONFIG_INVALID）
    bad = _create("bad-rule", admin, config={"run_error": {"enabled": True}})
    assert bad.status_code == 422
    assert "缺少规则段" in bad.json()["detail"]
    # name 空 → 422
    empty = _create("  ", admin)
    assert empty.status_code == 422
    assert "模板名称不能为空" in empty.json()["detail"]
    # 合法创建后重名 → 409（RULE_TEMPLATE_NAME_CONFLICT）
    assert _create("dup-rule", admin).status_code == 200
    dup = _create("dup-rule", admin)
    assert dup.status_code == 409
    assert "dup-rule" in dup.json()["detail"]


# U1223 ---------------------------------------------------------------------
def test_update_replaces_fields_preserving_id_and_created_at():
    admin = _headers()
    assert _create("upd-rule", admin).status_code == 200
    original = client.get("/api/alert-rule-templates/urt-1", headers=admin).json()
    new_config = {**VALID_CONFIG, "consecutive_failures": {"enabled": True, "threshold": 5}}
    updated = client.put(
        "/api/alert-rule-templates/urt-1",
        json={"name": "upd-rule-2", "description": "e", "tags": ["sre"], "config": new_config},
        headers=admin,
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["id"] == original["id"] and body["created_at"] == original["created_at"]
    assert body["name"] == "upd-rule-2" and body["config"]["consecutive_failures"]["threshold"] == 5
    assert body["source"] == "user"
    # 内置 id 不可改 → 404
    assert (
        client.put(
            "/api/alert-rule-templates/strict-sre",
            json={"name": "x", "description": "", "tags": [], "config": VALID_CONFIG},
            headers=admin,
        ).status_code
        == 404
    )
    # 未知 id → 404
    assert (
        client.put(
            "/api/alert-rule-templates/urt-99",
            json={"name": "x", "description": "", "tags": [], "config": VALID_CONFIG},
            headers=admin,
        ).status_code
        == 404
    )
    # 撞名 → 409
    assert _create("upd-rival", admin).status_code == 200
    conflict = client.put(
        "/api/alert-rule-templates/urt-1",
        json={"name": "upd-rival", "description": "", "tags": [], "config": VALID_CONFIG},
        headers=admin,
    )
    assert conflict.status_code == 409


# U1224 ---------------------------------------------------------------------
def test_delete_user_template_and_protect_builtin():
    admin = _headers()
    assert _create("del-rule", admin).status_code == 200
    deleted = client.delete("/api/alert-rule-templates/urt-1", headers=admin)
    assert deleted.status_code == 200 and deleted.json() == {"deleted": True}
    assert client.get("/api/alert-rule-templates/urt-1", headers=admin).status_code == 404
    # 再删 → 404
    assert client.delete("/api/alert-rule-templates/urt-1", headers=admin).status_code == 404
    # 内置 id 不可删 → 404
    assert client.delete("/api/alert-rule-templates/strict-sre", headers=admin).status_code == 404


# U1225 ---------------------------------------------------------------------
def test_cross_tenant_isolation():
    tenant_registry.reset_tenant("t1")
    admin_t1 = _headers()
    assert _create("t1-rule", admin_t1).status_code == 200
    admin_t2 = _headers("admin-b", "admin123")
    # t2 读不到 t1 的用户模板（跨租户 404）；列表只有内置
    assert client.get("/api/alert-rule-templates/urt-1", headers=admin_t2).status_code == 404
    items = client.get("/api/alert-rule-templates", headers=admin_t2).json()["items"]
    assert {item["id"] for item in items} == EXPECTED_IDS


# U1226 ---------------------------------------------------------------------
def test_write_endpoints_require_administer():
    client.cookies.clear()  # 打包 ZQ Q4：httpOnly Cookie 由前序测试残留
    viewer = _headers("viewer-a", "viewer123")
    payload = {"name": "v", "description": "", "tags": [], "config": VALID_CONFIG}
    assert client.post("/api/alert-rule-templates", json=payload, headers=viewer).status_code == 403
    assert client.put("/api/alert-rule-templates/urt-1", json=payload, headers=viewer).status_code == 403
    assert client.delete("/api/alert-rule-templates/urt-1", headers=viewer).status_code == 403
