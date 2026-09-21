"""U228 UserStore 纯逻辑与会话吊销（ADR T25，docs/31 §2/§7）。"""

from __future__ import annotations

from atlas.iam.accounts import UserExists, UserStore
from atlas.iam.passwords import verify_password
from atlas.iam.principals import Role
from atlas.iam.sessions import SessionStore


def _principal(store: SessionStore, tenant: str, username: str, role: Role):
    from atlas.iam.principals import Principal

    principal = Principal(
        tenant_id=tenant,
        tenant_name="演示企业 A",
        username=username,
        display_name=username,
        role=role,
    )
    return store.issue(principal), principal


def test_seed_inserts_and_is_idempotent() -> None:
    store = UserStore()
    assert store.seed() == 4
    assert store.seed() == 0
    assert len(store.list("t1")) == 3
    assert store.get("t1", "admin-a") is not None
    assert store.get_by_username("admin-b") is not None


def test_seed_never_overwrites_existing() -> None:
    store = UserStore()
    store.seed()
    updated = store.set_password("t1", "admin-a", "brand-new-pw")
    assert updated is not None
    assert store.seed() == 0
    account = store.get("t1", "admin-a")
    assert account is not None
    assert verify_password("brand-new-pw", account.password_hash)
    assert not verify_password("admin123", account.password_hash)


def test_create_and_duplicate_rejected() -> None:
    store = UserStore()
    account = store.create(
        tenant_id="t1",
        username="new-user",
        password="some-password",
        display_name="新用户",
        role=Role.OPERATOR,
    )
    assert account.status == "active"
    try:
        store.create(
            tenant_id="t1",
            username="new-user",
            password="other-password",
            display_name="重复",
            role=Role.VIEWER,
        )
        raise AssertionError("expected UserExists")
    except UserExists:
        pass


def test_update_fields_and_unknown_returns_none() -> None:
    store = UserStore()
    store.seed()
    updated = store.update(
        "t1", "operator-a", display_name="改名运营", role=Role.ADMIN, status="disabled"
    )
    assert updated is not None
    assert updated.display_name == "改名运营"
    assert updated.role == Role.ADMIN
    assert updated.status == "disabled"
    assert store.update("t1", "nobody", display_name="x") is None


def test_disable_revokes_sessions() -> None:
    sessions = SessionStore()
    store = UserStore(sessions)
    store.seed()
    token, _ = _principal(sessions, "t1", "viewer-a", Role.VIEWER)
    assert sessions.principal_for_token(token) is not None
    store.update("t1", "viewer-a", status="disabled")
    assert sessions.principal_for_token(token) is None


def test_set_password_revokes_sessions_and_old_password_fails() -> None:
    sessions = SessionStore()
    store = UserStore(sessions)
    store.seed()
    token, _ = _principal(sessions, "t1", "operator-a", Role.OPERATOR)
    updated = store.set_password("t1", "operator-a", "rotated-password")
    assert updated is not None
    assert verify_password("rotated-password", updated.password_hash)
    assert not verify_password("operator123", updated.password_hash)
    assert sessions.principal_for_token(token) is None


def test_revoke_for_user_keeps_named_token() -> None:
    sessions = SessionStore()
    token1, _ = _principal(sessions, "t1", "operator-a", Role.OPERATOR)
    token2, _ = _principal(sessions, "t1", "operator-a", Role.OPERATOR)
    sessions.revoke_for_user("t1", "operator-a", keep_token=token1)
    assert sessions.principal_for_token(token1) is not None
    assert sessions.principal_for_token(token2) is None
