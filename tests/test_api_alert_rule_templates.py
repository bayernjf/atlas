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
