# -*- coding: utf-8 -*-
"""打包 AV（docs/95）：首登强制改密的服务端强制位，U1141–U1144、U1146。

U1140/U1145（迁移 042 幂等、两档逐键一致）在
`tests/test_password_rotation_pg_integration.py`——那两条要真 PG。

这批的核心判据不是"门存在"，而是**合法路径仍然走得通**：本仓在这类门上吃过两次亏
（J-1c 把"弱口令能登"改成"没人能登"；打包 P 的门把 ATLAS_ENV 当裸字符串比，PROD 读成"非 prod"）。
所以每条 403 都配一条"改密后同一请求 200"的判别对照，每条拒绝都必须给出可执行的下一步
（`/api/auth/*` 在未改密时**始终可达**，U1143——挡住它就是自锁死锁）。
"""

from __future__ import annotations

import inspect
import json
import os
import uuid

import pytest
from fastapi import Depends
from fastapi.params import Depends as DependsSpec
from fastapi.testclient import TestClient
from starlette.routing import Mount

from atlas.api.main import app
from atlas.iam.deps import CODE_PASSWORD_CHANGE_REQUIRED, get_principal, user_store

anon = TestClient(app)

# 前端按 code 走 i18n（docs/17 §2.4），所以测试里的字面量必须与后端常量同值——
# test_u1146_code_string_is_shared_not_forked 钉这件事，不靠人记。
CODE = "AUTH_PASSWORD_CHANGE_REQUIRED"

# 出示平台凭证却不经 require() 的端点＝强制位的豁免面。三条 auth 是自解装置，
# /api/feedback 是刻意豁免：它不读业务数据，而被卡住的人正好需要它报障。
AUTH_ALLOWLIST = {
    "POST /api/auth/logout",
    "GET /api/auth/me",
    "POST /api/auth/change-password",
    "POST /api/feedback",
}

# 覆盖三档能力：read / read / read / read / administer——最后一条专门验证
# "轮换门排在角色门之前"（未改密时先告诉运营该改密，而不是让其对着"角色无权"猜）。
BUSINESS_ENDPOINTS = [
    ("get", "/api/graphs", None),
    ("get", "/api/adapters", None),
    ("get", "/api/monitoring/metrics", None),
    ("get", "/api/schedules", None),
    ("get", "/api/users", None),
]


@pytest.fixture(autouse=True)
def _neutral_profile():
    """每条用例自带档位，不继承机器上残留的 ATLAS_ENV。"""
    saved = os.environ.get("ATLAS_ENV")
    saved_mock = os.environ.get("ATLAS_ENABLE_DEMO_MOCK")
    os.environ["ATLAS_ENV"] = "dev"
    os.environ.pop("ATLAS_ENABLE_DEMO_MOCK", None)
    yield
    if saved is None:
        os.environ.pop("ATLAS_ENV", None)
    else:
        os.environ["ATLAS_ENV"] = saved
    if saved_mock is None:
        os.environ.pop("ATLAS_ENABLE_DEMO_MOCK", None)
    else:
        os.environ["ATLAS_ENABLE_DEMO_MOCK"] = saved_mock


def _as_prod() -> None:
    os.environ["ATLAS_ENV"] = "prod"


def _login(username: str, password: str) -> tuple[dict[str, str], dict]:
    resp = anon.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    return {"Authorization": f"Bearer {body['token']}"}, body


def _admin() -> dict[str, str]:
    headers, _ = _login("admin-a", "admin123")
    return headers


def _create_account(role: str = "admin", password: str = "Strongpass-1") -> str:
    """走真实路径建号。prod 里 admin 播的是引导口令、operator/viewer 全靠这条路径产生，
    所以「别人替你设的口令」就是生产账号的初始态。"""
    username = f"av-{uuid.uuid4().hex[:12]}"
    resp = anon.post(
        "/api/users",
        headers=_admin(),
        json={
            "username": username,
            "password": password,
            "displayName": "打包 AV 用例",
            "role": role,
        },
    )
    assert resp.status_code == 201, resp.text
    return username


def _rotate(headers: dict[str, str], old: str, new: str = "Changedpass-3"):
    return anon.post(
        "/api/auth/change-password",
        headers=headers,
        json={"oldPassword": old, "newPassword": new},
    )


# --- U1141 轮换位的盖章与未盖章 ---------------------------------------------


def test_u1141_admin_created_account_is_unrotated_then_stamped_by_its_owner() -> None:
    username = _create_account()

    created = user_store.get_by_username(username)
    assert created is not None
    assert created.password_rotated_at is None, "admin 建的号必须未盖章"

    _as_prod()
    headers, body = _login(username, "Strongpass-1")
    assert body["mustChangePassword"] is True

    assert _rotate(headers, "Strongpass-1").status_code == 200
    stamped = user_store.get_by_username(username)
    assert stamped is not None and stamped.password_rotated_at is not None, (
        "改密成功必须把时刻写进行里——只改响应不写行，下次登录又会被强制"
    )

    _, relogin = _login(username, "Changedpass-3")
    assert relogin["mustChangePassword"] is False


def test_u1141_admin_reset_rearms_the_gate() -> None:
    """admin 重置＝口令又回到第三方手里，强制位回到未满足态（docs/95 §2 的 D-1 订正）。

    顺带量出 D-2(b) 的另一面：**未改密的 admin 自己也打不开 reset-password**（它经过
    require()），所以 prod 的开工顺序被强制成"先改自己的引导口令，再管别人"。这条用
    已改密的 admin 当操作者，就是把那个顺序测出来。
    """
    operator = _create_account()  # dev 里建号、改密，得到一个"已轮换"的操作者
    operator_headers, _ = _login(operator, "Strongpass-1")
    assert _rotate(operator_headers, "Strongpass-1").status_code == 200
    target = _create_account()

    _as_prod()
    admin_headers, _ = _login(operator, "Changedpass-3")
    headers, body = _login(target, "Strongpass-1")
    assert body["mustChangePassword"] is True
    assert _rotate(headers, "Strongpass-1").status_code == 200
    assert user_store.get_by_username(target).password_rotated_at is not None

    assert anon.post(
        f"/api/users/{target}/reset-password",
        headers=admin_headers,
        json={"newPassword": "Resetpass-2"},
    ).status_code == 200
    assert user_store.get_by_username(target).password_rotated_at is None

    _, body = _login(target, "Resetpass-2")
    assert body["mustChangePassword"] is True


def test_u1142_unrotated_admin_cannot_administer() -> None:
    """未改密的 admin 打不开 administer 端点——轮换门在角色门之前，且不分档。"""
    username = _create_account()
    _as_prod()
    headers, _ = _login(username, "Strongpass-1")
    resp = anon.get("/api/users", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == CODE, "该报「改密」而不是「角色无权」"


# --- U1142 强制生效＋判别对照 ------------------------------------------------


def test_u1142_business_endpoints_refuse_until_the_password_is_changed() -> None:
    username = _create_account()
    _as_prod()
    blocked, _ = _login(username, "Strongpass-1")

    for method, path, payload in BUSINESS_ENDPOINTS:
        resp = anon.request(method, path, json=payload, headers=blocked)
        assert resp.status_code == 403, f"{method.upper()} {path} → {resp.status_code}"
        detail = resp.json()["detail"]
        assert detail["code"] == CODE, f"{path}: {detail}"
        assert "密码" in detail["message"], "拒绝必须给出可执行的下一步"

    # 判别对照：改密后同一批请求逐条 200。缺了它，"永远 403" 也能让上面全绿。
    rotating, _ = _login(username, "Strongpass-1")
    assert _rotate(rotating, "Strongpass-1").status_code == 200
    for method, path, payload in BUSINESS_ENDPOINTS:
        resp = anon.request(method, path, json=payload, headers=rotating)
        assert resp.status_code == 200, f"{method.upper()} {path} → {resp.status_code}"


def test_u1142_gate_applies_at_the_read_tier_too() -> None:
    """D-2 选 (b)：不分能力档。viewer 只有 read，read 一样被挡。"""
    username = _create_account(role="viewer")
    _as_prod()
    headers, _ = _login(username, "Strongpass-1")
    resp = anon.get("/api/graphs", headers=headers)
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == CODE, "read 档不能退化成 AUTH_FORBIDDEN"


# --- U1143 自解装置必须始终可达 ----------------------------------------------


def test_u1143_auth_routes_stay_reachable_while_blocked() -> None:
    username = _create_account()
    _as_prod()
    headers, _ = _login(username, "Strongpass-1")

    assert anon.get("/api/auth/me", headers=headers).status_code == 200
    assert _rotate(headers, "Strongpass-1").status_code == 200
    # 改密保留当前会话：这里出示的是 Bearer，keep_token 取"本次出示的凭证"。
    assert anon.get("/api/auth/me", headers=headers).status_code == 200
    assert anon.get("/api/graphs", headers=headers).status_code == 200
    assert anon.post("/api/auth/logout", headers=headers).status_code == 200


def test_u1143_change_password_keeps_the_cookie_session_alive() -> None:
    """Cookie 客户端改密后当前会话仍可用——这才是 docstring 承诺的「保留当前」。

    旧实现把 keep_token 取成"只认 Authorization 头"的那一份，而 `get_principal` 的取数规则
    是"带头就以头为准，否则读 Cookie"；打包 ZQ Q4 之后 SPA 不带 Authorization 头，于是改密
    会把自己也吊销、改完即 401。强制改密弹框的正路是"改完立刻继续用平台"，这条把它钉住。
    现在两处共用 `iam.deps.presented_token`，只有一份取数规则。
    """
    username = _create_account()
    resp = anon.post("/api/auth/login", json={"username": username, "password": "Strongpass-1"})
    assert resp.status_code == 200
    assert anon.cookies.get("atlas_session"), "登录必须下发 httpOnly 会话 Cookie"

    changed = anon.post(
        "/api/auth/change-password",
        json={"oldPassword": "Strongpass-1", "newPassword": "Changedpass-3"},
    )
    assert changed.status_code == 200, changed.text
    body = anon.get("/api/auth/me").json()
    assert body["mustChangePassword"] is False, "Cookie 会话不该被自己的改密吊销"
    anon.post("/api/auth/logout")


def test_u1143_both_credentials_presented_keeps_the_one_that_authenticated() -> None:
    """同时出示 Cookie 与 Bearer 时，"保留当前"保留的是**用来认证的那一条**。

    这条是对旧 bug 形状的对拍：`get_principal` 带头就以头为准，而 keep_token 那一份抄的是
    Cookie 优先——两个规则并存时，改密会保留住没在用的那个会话、踢掉正在用的那个。
    现在两处共用 `presented_token`，所以这个用例是"共用"这件事的判别对照。
    """
    username = _create_account()
    _, first = _login(username, "Strongpass-1")
    bearer_headers, bearer_token = _login(username, "Strongpass-1")
    cookie_client = TestClient(app)  # 独立 jar：per-request cookies= 已被 httpx 弃用
    cookie_client.cookies.set("atlas_session", first["token"])
    assert bearer_token != first["token"]

    resp = anon.post(
        "/api/auth/change-password",
        headers=bearer_headers,
        json={"oldPassword": "Strongpass-1", "newPassword": "Changedpass-3"},
    )
    assert resp.status_code == 200, resp.text
    assert anon.get("/api/auth/me", headers=bearer_headers).status_code == 200, (
        "出示的 Bearer 才是当前会话，必须活下来"
    )
    assert cookie_client.get("/api/auth/me").status_code == 401, (
        "没被用来认证的那条 Cookie 才该被吊销"
    )


# --- U1144 非 prod 与演示面逐键不变 ------------------------------------------


@pytest.mark.parametrize("profile,mock", [("dev", None), ("test", None), ("prod", "1")])
def test_u1144_non_prod_and_demo_surface_never_enforce(profile: str, mock) -> None:
    username = _create_account()
    os.environ["ATLAS_ENV"] = profile
    if mock is not None:
        os.environ["ATLAS_ENABLE_DEMO_MOCK"] = mock

    headers, body = _login(username, "Strongpass-1")
    assert body["mustChangePassword"] is False, f"{profile}+mock={mock} 不该强制"
    for method, path, payload in BUSINESS_ENDPOINTS:
        resp = anon.request(method, path, json=payload, headers=headers)
        assert resp.status_code == 200, f"{method.upper()} {path} → {resp.status_code}"


# --- U1146 机检：认证但不走 require() 的端点必须被点名 ------------------------


def _walk(routes, found: set[str]) -> None:
    for route in routes:
        inner = getattr(route, "original_router", None)
        if inner is not None:
            _walk(getattr(inner, "routes", []), found)
            continue
        if isinstance(route, Mount):
            continue
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        for param in inspect.signature(endpoint).parameters.values():
            # fastapi.Depends 在 0.141 是**函数**，实例类型是 fastapi.params.Depends——
            # `isinstance(x, fastapi.Depends)` 直接 TypeError，守护别写成那样。
            if isinstance(param.default, DependsSpec) and param.default.dependency is get_principal:
                for method in sorted((getattr(route, "methods", None) or set()) - {"HEAD"}):
                    found.add(f"{method} {route.path}")
                break


def _ungated_authenticated_surface() -> set[str]:
    """出示平台凭证、却**没有**经过 require()（因此没有轮换门）的端点。

    require() 是唯一强制点，所以"绕过 require()"＝"绕过强制位"。`include_router` 的
    端点藏在 `_IncludedRouter` 容器里，平铺遍历对它失明（docs/89 §12 N-2 的教训）。
    """
    found: set[str] = set()
    _walk(app.routes, found)
    return found


def test_u1146_ungated_authenticated_surface_is_exactly_the_allowlist() -> None:
    assert _ungated_authenticated_surface() == AUTH_ALLOWLIST, (
        "新增业务端点若直接 Depends(get_principal) 就绕过了首登强制改密门；"
        "要么改用 require()，要么把它连同理由加进 AUTH_ALLOWLIST"
    )


def test_u1146_reverse_gate_a_new_bypassing_endpoint_is_caught() -> None:
    """反向门：植一条绕过 require() 的假端点，守护必须点名。"""
    from fastapi.responses import JSONResponse

    def _probe(principal=Depends(get_principal)) -> JSONResponse:
        return JSONResponse({"probe": True})

    before = _ungated_authenticated_surface()
    app.add_api_route("/api/__av_u1146_probe__", _probe, methods=["GET"])
    try:
        after = _ungated_authenticated_surface()
        assert "GET /api/__av_u1146_probe__" in after, "植了缺陷守护却没红＝这条守护不可信"
        assert after - before == {"GET /api/__av_u1146_probe__"}
    finally:
        app.router.routes = [
            r for r in app.router.routes if getattr(r, "path", "") != "/api/__av_u1146_probe__"
        ]
    assert _ungated_authenticated_surface() == before, "探针没摘干净会污染后续用例"


def test_u1146_code_string_is_shared_not_forked() -> None:
    """新错误码必须**同时**有前端文案——A-4 的教训：只落后端就会在英文态露中文。

    这里不只看常量，还回读前端源文件：`AUTH_PASSWORD_CHANGE_REQUIRED` 必须出现在
    apiClient 的 code→i18n 映射里，且 `users.forceChange` 文案在两档 locale 都存在。
    """
    from pathlib import Path

    assert CODE == CODE_PASSWORD_CHANGE_REQUIRED
    assert CODE.startswith("AUTH_")

    frontend = Path(__file__).resolve().parents[1] / "frontend" / "src"
    api_client = (frontend / "lib" / "apiClient.ts").read_text(encoding="utf-8")
    assert CODE_PASSWORD_CHANGE_REQUIRED in api_client, "后端发了码，前端不认识＝静默显示原文"
    assert "error.auth.passwordChangeRequired" in api_client

    for locale_dir in ("zh-CN", "en-US"):
        catalog = json.loads((frontend / "locales" / locale_dir / "common.json").read_text(encoding="utf-8"))
        assert catalog["error"]["auth"]["passwordChangeRequired"], locale_dir
        assert {"title", "body", "submit", "signOut"} <= set(catalog["users"]["forceChange"]), locale_dir
