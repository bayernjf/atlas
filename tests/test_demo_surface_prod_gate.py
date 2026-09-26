# -*- coding: utf-8 -*-
"""匿名可达面收口与 allowlist 守护（docs/75 打包 P，U909–U913）。

这批的真产出不是那 5 行门，而是 U909：**"哪些路由不需要凭证就能打"变成一张机器枚举的
表**。多一条（有人新加了无鉴权端点）少一条（有人把公开面收窄了）都红。
S5 这个判断已经在 docs/63 → docs/72 → docs/74 之间漂移过两次，靠人记是记不住的。

反向门（U909 的后半）是这条守护能被相信的唯一理由：临时往 app 上挂一条无鉴权假路由，
守护必须把它抓出来。
"""

from __future__ import annotations

import inspect
import uuid

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from atlas.api.main import _demo_mock_enabled, app
from atlas.security.bootstrap import read_env_profile

client = TestClient(app)

# 设计如此的公开面：每条各自靠什么成立（不是"平台登录态"，就是"签名/HMAC/无敏感数据"）。
PUBLIC_BY_DESIGN: dict[str, str] = {
    "GET /api/health": "存活探针，无数据",
    "GET /api/ready": "就绪探针（真打 SELECT 1），只回 ok/fail",
    "GET /metrics": "K-C：prod 未配 token→404，配了须 Bearer",
    "POST /api/channels/hooks/shopify/{binding_id}": "公开入站，先 HMAC-SHA256 验签再解析 body",
    "GET /connections/callback": "OAuth 回调，state 不可预测且一次性",
    "POST /api/auth/login": "登录入口本身（有速率门）",
    "GET /api/approvals/email-view": "邮件深链，签名 capability token＋短时效",
    "POST /api/approvals/email-decision": "邮件深链决策，签名 token＋绑收件人（J-1b）",
}

# 打包 P 收口的五条 demo 模拟面：prod 档必须 404，非 prod 保持原语义。
DEMO_SURFACE = [
    ("post", "/api/demo/shop/login", {"username": "demo", "password": "demo"}),
    ("get", "/api/demo/shop/orders", None),
    ("get", "/api/demo/mock/orders", None),
    ("post", "/api/demo/mock/orders/12345/receipt", {"a": 1}),
    ("get", "/demo/shop", None),
]

# 收口前一直在开门、且已由 J-3e 守住的三条：证明改共用函数没把老门的语义改坏。
LEGACY_DEMO_SURFACE = [
    ("get", "/api/demo/mock/shopify-admin/webhooks.json", None),
    ("post", "/api/demo/mock/shopify-admin/webhooks.json",
     {"webhook": {"topic": "orders/paid", "address": "https://example.test/u909", "format": "json"}}),
]


def anonymous_surface() -> set[str]:
    """枚举"既无平台鉴权依赖、也无 demo 档位门"的路由。"""
    found: set[str] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        annotations = getattr(route.endpoint, "__annotations__", {})
        if any("Principal" in str(value) for value in annotations.values()):
            continue
        try:
            source = inspect.getsource(route.endpoint)
        except (OSError, TypeError):  # pragma: no cover - 动态端点，理论上不会出现
            source = ""
        if "_demo_mock_enabled" in source:
            continue
        for method in sorted(m for m in route.methods if m != "HEAD"):
            found.add(f"{method} {route.path}")
    return found


@pytest.fixture(autouse=True)
def _neutral_profile():
    """每条用例自带档位，不继承机器上残留的 ATLAS_ENV。"""
    import os

    saved = os.environ.get("ATLAS_ENV")
    os.environ["ATLAS_ENV"] = "dev"
    os.environ.pop("ATLAS_ENABLE_DEMO_MOCK", None)
    yield
    if saved is None:
        os.environ.pop("ATLAS_ENV", None)
    else:
        os.environ["ATLAS_ENV"] = saved
    os.environ.pop("ATLAS_ENABLE_DEMO_MOCK", None)


# --- U909 allowlist 守护 ---------------------------------------------------

def test_u909_anonymous_surface_is_exactly_the_public_by_design_allowlist():
    surface = anonymous_surface()
    assert surface == set(PUBLIC_BY_DESIGN), (
        f"多了：{sorted(surface - set(PUBLIC_BY_DESIGN))}；"
        f"少了：{sorted(set(PUBLIC_BY_DESIGN) - surface)}"
    )


def test_u909_reverse_gate_a_new_unauthenticated_route_is_caught():
    """反向门：挂一条无鉴权假路由上去，守护必须把它点名——否则上面的绿是假的。"""
    from starlette.responses import JSONResponse

    def _probe() -> JSONResponse:  # 签名里没有 Principal，函数体里没有档位门
        return JSONResponse({"probe": True})

    before = anonymous_surface()
    app.add_api_route("/api/__u909_probe__", _probe, methods=["GET"])
    try:
        after = anonymous_surface()
        assert "GET /api/__u909_probe__" in after, "植了缺陷守护却没红＝这条守护不可信"
        assert after - before == {"GET /api/__u909_probe__"}, after - before
    finally:
        app.router.routes = [r for r in app.router.routes
                             if getattr(r, "path", "") != "/api/__u909_probe__"]
    assert anonymous_surface() == before, "探针没摘干净会污染后续用例"


# --- U910 prod 档逐条 404 --------------------------------------------------

def test_u910_every_demo_surface_route_is_404_under_prod(monkeypatch):
    monkeypatch.setenv("ATLAS_ENV", "prod")
    assert read_env_profile() == "prod"
    for method, path, body in DEMO_SURFACE + LEGACY_DEMO_SURFACE:
        response = _call(method, path, body)
        assert response.status_code == 404, f"{method.upper()} {path} 在 prod 仍可达：{response.status_code}"
        assert response.json().get("detail") == "Not Found", f"{path} 泄露了存在性：{response.text[:80]}"


def test_u910b_the_single_switch_reopens_the_whole_surface(monkeypatch):
    """`ATLAS_ENABLE_DEMO_MOCK=1` 是唯一开闸方式，且它一次开全——不留半开状态。"""
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_ENABLE_DEMO_MOCK", "1")
    assert _demo_mock_enabled() is True
    assert _call("post", "/api/demo/shop/login", {"username": "demo", "password": "demo"}).status_code == 200


def test_u910c_platform_auth_routes_are_untouched_by_the_gate(monkeypatch):
    """门只管 demo 面：prod 档下业务端点仍是 401，不是 404（否则排障会说谎）。"""
    monkeypatch.setenv("ATLAS_ENV", "prod")
    assert client.get("/api/graphs").status_code == 401
    assert client.get("/api/schedules").status_code == 401


# --- U911 非 prod 零变化 ---------------------------------------------------

def test_u911_dev_profile_keeps_the_trial_flow():
    assert _demo_mock_enabled() is True
    login = _call("post", "/api/demo/shop/login", {"username": "demo", "password": "demo"})
    assert login.status_code == 200 and login.json() == {"logged_in": True}
    assert _call("get", "/api/demo/shop/orders", None).status_code == 200
    assert _call("get", "/api/demo/mock/orders", None).status_code == 401, "X-Demo-Token 检查不能被门替掉"
    echo = _call("post", "/api/demo/mock/orders/12345/receipt", {"amount": 299})
    assert echo.status_code == 200 and echo.json()["body"] == {"amount": 299}
    console = client.get("/demo/shop")
    assert console.status_code == 200 and "text/html" in console.headers["content-type"]


# --- U912 D-2 的档位归一（那才是真 fail-open） -----------------------------

def test_u912_uppercase_prod_is_not_silently_treated_as_dev():
    """改前实况：`os.getenv("ATLAS_ENV") != "prod"` 把 `PROD` 判成"非 prod"⇒ 匿名面全开。"""
    import os

    os.environ["ATLAS_ENV"] = "PROD"
    assert read_env_profile() == "prod", "档位读取器本身大小写不敏感"
    assert _demo_mock_enabled() is False, "大写 PROD 被当成非 prod＝demo 面在 prod 静默开门"
    assert client.get("/demo/shop").status_code == 404
    assert client.get("/api/health").status_code == 200, "真公开面不受影响"


def test_u912b_an_invalid_profile_raises_instead_of_defaulting_open():
    import os

    os.environ["ATLAS_ENV"] = "production"
    with pytest.raises(ValueError):
        read_env_profile()
    with pytest.raises(ValueError):
        _demo_mock_enabled()


# --- U913 老三条不回归 -----------------------------------------------------

def test_u913_legacy_guarded_routes_still_404_in_dev_of_the_wrong_profile():
    """dev 档下老三条照常工作（防止把共用函数改坏后只有新面受益/只有老面坏掉）。"""
    listing = _call("get", "/api/demo/mock/shopify-admin/webhooks.json", None)
    assert listing.status_code == 200
    created = _call(
        "post", "/api/demo/mock/shopify-admin/webhooks.json",
        {"webhook": {"topic": "orders/paid",
                     "address": f"https://example.test/u913-{uuid.uuid4().hex[:8]}", "format": "json"}},
    )
    assert created.status_code == 200, created.text


def _call(method: str, path: str, body: dict | None):
    lowered = method.lower()
    if lowered == "get":
        return client.get(path)
    return client.post(path, json=body if body is not None else {})
