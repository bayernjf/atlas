# -*- coding: utf-8 -*-
"""U231（用户部分）：账号生命周期 REST（docs/31 §3，ADR T25）。

改密/用户 CRUD/停用/重置的状态码与会话吊销；TTL 与节流部分随步骤 5/6 补入。
所有测试用户带 uuid 后缀，避免污染共享内存 user_store。
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from atlas.api.main import app

anon = TestClient(app)


def _login(username: str, password: str) -> tuple[dict[str, str], str]:
    resp = anon.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}, token


def _admin_a() -> dict[str, str]:
    headers, _ = _login("admin-a", "admin123")
    return headers


def _admin_b() -> dict[str, str]:
    headers, _ = _login("admin-b", "admin123")
    return headers


def _new_username() -> str:
    return f"u-{uuid.uuid4().hex[:12]}"


def test_list_users_admin_only_and_never_exposes_hash() -> None:
    admin = _admin_a()
    resp = anon.get("/api/users", headers=admin)
    assert resp.status_code == 200
    users = resp.json()
    assert any(user["username"] == "admin-a" for user in users)
    sample = next(user for user in users if user["username"] == "admin-a")
    assert "password_hash" not in sample
    assert "passwordHash" not in sample
    assert sample["role"] == "admin"
    assert sample["status"] == "active"
    assert sample["createdAt"]
    assert sample["updatedAt"]

    viewer, _ = _login("viewer-a", "viewer123")
    assert anon.get("/api/users", headers=viewer).status_code == 403


def test_create_user_rejects_bad_policy_and_conflict() -> None:
    admin = _admin_a()

    def create(body: dict) -> int:
        return anon.post("/api/users", headers=admin, json=body).status_code

    base_username = _new_username()
    assert create(
        {"username": "ab", "password": "Strongpass-1", "displayName": "x", "role": "viewer"}
    ) == 422
    assert create(
        {"username": "Bad_User", "password": "Strongpass-1", "displayName": "x", "role": "viewer"}
    ) == 422
    assert create(
        {"username": base_username, "password": "short", "displayName": "x", "role": "viewer"}
    ) == 422
    assert create(
        {"username": base_username, "password": "admin123", "displayName": "x", "role": "viewer"}
    ) == 422
    assert create(
        {"username": base_username, "password": "Strongpass-1", "displayName": "  ", "role": "viewer"}
    ) == 422

    created = anon.post(
        "/api/users",
        headers=admin,
        json={
            "username": base_username,
            "password": "Strongpass-1",
            "displayName": "U231 新用户",
            "role": "operator",
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body["username"] == base_username
    assert body["displayName"] == "U231 新用户"
    assert body["role"] == "operator"
    assert body["status"] == "active"

    conflict = anon.post(
        "/api/users",
        headers=admin,
        json={
            "username": base_username,
            "password": "Strongpass-2",
            "displayName": "重复",
            "role": "viewer",
        },
    )
    assert conflict.status_code == 409

    headers, _ = _login(base_username, "Strongpass-1")
    assert anon.get("/api/auth/me", headers=headers).status_code == 200


def test_patch_user_is_scoped_and_updates_fields() -> None:
    admin_a = _admin_a()
    username = _new_username()
    created = anon.post(
        "/api/users",
        headers=admin_a,
        json={
            "username": username,
            "password": "Strongpass-1",
            "displayName": "编辑前",
            "role": "viewer",
        },
    )
    assert created.status_code == 201

    assert anon.patch(
        f"/api/users/{username}", headers=_admin_b(), json={"displayName": "跨租户"}
    ).status_code == 404
    assert anon.patch(
        "/api/users/nobody-x", headers=admin_a, json={"displayName": "不存在"}
    ).status_code == 404

    patched = anon.patch(
        f"/api/users/{username}",
        headers=admin_a,
        json={"displayName": "编辑后", "role": "operator", "status": "disabled"},
    )
    assert patched.status_code == 200
    body = patched.json()
    assert body["displayName"] == "编辑后"
    assert body["role"] == "operator"
    assert body["status"] == "disabled"

    viewer, _ = _login("viewer-a", "viewer123")
    assert anon.patch(
        f"/api/users/{username}", headers=viewer, json={"displayName": "越权"}
    ).status_code == 403


def test_reset_password_revokes_existing_sessions() -> None:
    admin = _admin_a()
    username = _new_username()
    anon.post(
        "/api/users",
        headers=admin,
        json={
            "username": username,
            "password": "Strongpass-1",
            "displayName": "重置对象",
            "role": "viewer",
        },
    ).raise_for_status()
    user_headers, _ = _login(username, "Strongpass-1")
    assert anon.get("/api/auth/me", headers=user_headers).status_code == 200

    assert anon.post(
        "/api/users/nobody-x/reset-password", headers=admin, json={"newPassword": "Rotatedpass-2"}
    ).status_code == 404
    weak = anon.post(
        f"/api/users/{username}/reset-password",
        headers=admin,
        json={"newPassword": "admin123"},
    )
    assert weak.status_code == 422

    reset = anon.post(
        f"/api/users/{username}/reset-password",
        headers=admin,
        json={"newPassword": "Rotatedpass-2"},
    )
    assert reset.status_code == 200
    assert anon.get("/api/auth/me", headers=user_headers).status_code == 401
    assert anon.post(
        "/api/auth/login", json={"username": username, "password": "Strongpass-1"}
    ).status_code == 401
    new_headers, _ = _login(username, "Rotatedpass-2")
    assert anon.get("/api/auth/me", headers=new_headers).status_code == 200


def test_disabled_user_sessions_revoked_and_login_403() -> None:
    admin = _admin_a()
    username = _new_username()
    anon.post(
        "/api/users",
        headers=admin,
        json={
            "username": username,
            "password": "Strongpass-1",
            "displayName": "停用对象",
            "role": "viewer",
        },
    ).raise_for_status()
    user_headers, _ = _login(username, "Strongpass-1")

    disabled = anon.patch(
        f"/api/users/{username}", headers=admin, json={"status": "disabled"}
    )
    assert disabled.status_code == 200
    assert disabled.json()["status"] == "disabled"
    assert anon.get("/api/auth/me", headers=user_headers).status_code == 401

    blocked = anon.post(
        "/api/auth/login", json={"username": username, "password": "Strongpass-1"}
    )
    assert blocked.status_code == 403
    assert blocked.json()["detail"] == "账号已停用，请联系管理员"

    anon.patch(f"/api/users/{username}", headers=admin, json={"status": "active"})
    relogin, _ = _login(username, "Strongpass-1")
    assert anon.get("/api/auth/me", headers=relogin).status_code == 200


def test_change_password_keeps_current_session_and_revokes_others() -> None:
    admin = _admin_a()
    username = _new_username()
    anon.post(
        "/api/users",
        headers=admin,
        json={
            "username": username,
            "password": "Strongpass-1",
            "displayName": "改密对象",
            "role": "operator",
        },
    ).raise_for_status()
    current, _ = _login(username, "Strongpass-1")
    other, _ = _login(username, "Strongpass-1")

    wrong_old = anon.post(
        "/api/auth/change-password",
        headers=current,
        json={"oldPassword": "not-the-password", "newPassword": "Changedpass-3"},
    )
    assert wrong_old.status_code == 400
    assert wrong_old.json()["detail"] == "原密码错误"

    weak_new = anon.post(
        "/api/auth/change-password",
        headers=current,
        json={"oldPassword": "Strongpass-1", "newPassword": "12345678"},
    )
    assert weak_new.status_code == 422
    same = anon.post(
        "/api/auth/change-password",
        headers=current,
        json={"oldPassword": "Strongpass-1", "newPassword": "Strongpass-1"},
    )
    assert same.status_code == 422

    changed = anon.post(
        "/api/auth/change-password",
        headers=current,
        json={"oldPassword": "Strongpass-1", "newPassword": "Changedpass-3"},
    )
    assert changed.status_code == 200
    assert changed.json() == {"changed": True}
    assert anon.get("/api/auth/me", headers=current).status_code == 200
    assert anon.get("/api/auth/me", headers=other).status_code == 401
    assert anon.post(
        "/api/auth/login", json={"username": username, "password": "Strongpass-1"}
    ).status_code == 401
    relogin, _ = _login(username, "Changedpass-3")
    assert anon.get("/api/auth/me", headers=relogin).status_code == 200


def test_login_throttle_429_after_five_failures_then_reset_on_success() -> None:
    username = _new_username()

    def login(password: str):
        return anon.post(
            "/api/auth/login", json={"username": username, "password": password}
        )

    for _ in range(5):
        assert login("bad-password").status_code == 401
    locked = login("bad-password")
    assert locked.status_code == 429
    assert locked.json()["detail"] == "登录尝试过于频繁，请稍后再试"
    # 锁定时不校验口令：用户不存在/口令错不再区分，计数也不再增长

    # 成功登录清零：另建一个真实用户，4 次失败后成功 → 计数归零
    real_user = _new_username()
    admin = _admin_a()
    anon.post(
        "/api/users",
        headers=admin,
        json={
            "username": real_user,
            "password": "Strongpass-1",
            "displayName": "节流恢复",
            "role": "viewer",
        },
    ).raise_for_status()
    for _ in range(4):
        resp = anon.post(
            "/api/auth/login", json={"username": real_user, "password": "wrong"}
        )
        assert resp.status_code == 401
    ok = anon.post(
        "/api/auth/login", json={"username": real_user, "password": "Strongpass-1"}
    )
    assert ok.status_code == 200
    again = anon.post(
        "/api/auth/login", json={"username": real_user, "password": "wrong"}
    )
    assert again.status_code == 401
