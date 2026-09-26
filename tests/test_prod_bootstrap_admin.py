# -*- coding: utf-8 -*-
"""prod 首任管理员引导口令（docs/66 打包 L，U860–U867；解 docs/63 §0A N1 登录死锁）。

本文件守的是两件事：
① prod 缺/弱引导口令 ⇒ 进程拒绝起来（fail-closed 真在承重）；
② dev/test 播种与登录行为逐键不变（防止"顺手收紧"把演示形态改坏）。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from atlas.iam import deps
from atlas.iam.passwords import WEAK_PASSWORDS
from atlas.iam.principals import (
    Role,
    SEED_USERS,
    seed_plan_for_profile,
)
from atlas.security.bootstrap import (
    PROD_BOOTSTRAP_PASSWORD_ENV,
    assert_prod_secrets,
    prod_bootstrap_password,
    read_env_profile,
)

BOOTSTRAP = "Str0ng-Bootstrap-Pw"
GOOD_KEY = "0123456789012345678901234567890123"  # 32 字节，只为过 assert_prod_secrets 的长度门


def _prod_env(monkeypatch, bootstrap: str | None) -> None:
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_MASTER_KEY", GOOD_KEY)
    monkeypatch.setenv("ATLAS_APPROVAL_HMAC_SECRET", GOOD_KEY)
    if bootstrap is None:
        monkeypatch.delenv(PROD_BOOTSTRAP_PASSWORD_ENV, raising=False)
    else:
        monkeypatch.setenv(PROD_BOOTSTRAP_PASSWORD_ENV, bootstrap)


# --- U860–U861 缺失与不合策略都拒启 ------------------------------------------


def test_u860_prod_without_bootstrap_password_refuses_to_start(monkeypatch):
    _prod_env(monkeypatch, None)
    with pytest.raises(RuntimeError) as exc:
        prod_bootstrap_password()
    assert PROD_BOOTSTRAP_PASSWORD_ENV in str(exc.value)


def test_u861_weak_bootstrap_password_refuses_to_start(monkeypatch):
    for bad in ("short", "password", "PASSWORD"):
        _prod_env(monkeypatch, bad)
        with pytest.raises(RuntimeError) as exc:
            prod_bootstrap_password()
        assert "口令策略" in str(exc.value), bad


def test_u861a_whitespace_only_counts_as_missing(monkeypatch):
    """strip 后为空＝缺失（走"缺少变量"那条消息），不是"策略不合"——两条路径要能分开排障。"""
    _prod_env(monkeypatch, "   ")
    with pytest.raises(RuntimeError) as exc:
        prod_bootstrap_password()
    assert "缺少" in str(exc.value)


def test_u861b_secret_gate_also_requires_the_bootstrap_value(monkeypatch):
    """两道门一处措辞：secrets 齐了但引导口令缺失，`assert_prod_secrets()` 仍拒绝。"""
    _prod_env(monkeypatch, None)
    with pytest.raises(RuntimeError):
        assert_prod_secrets()


def test_u861c_non_prod_ignores_the_bootstrap_variable(monkeypatch):
    monkeypatch.setenv("ATLAS_ENV", "dev")
    monkeypatch.delenv(PROD_BOOTSTRAP_PASSWORD_ENV, raising=False)
    assert prod_bootstrap_password() is None
    assert_prod_secrets()  # 不抛


# --- U862–U864 播种计划 -------------------------------------------------------


def test_u862_prod_plan_is_admins_only_with_the_bootstrap_password(monkeypatch):
    _prod_env(monkeypatch, BOOTSTRAP)
    plan = seed_plan_for_profile()
    assert [u.role for u in plan] == [Role.ADMIN, Role.ADMIN]
    assert {u.tenant_id for u in plan} == {"t1", "t2"}
    assert all(u.password == BOOTSTRAP for u in plan)
    # 关键：仓库内那四套明文口令一条都不播
    assert all(u.password not in {s.password for s in SEED_USERS} for u in plan)


def test_u863_prod_plan_logins_with_bootstrap_and_not_with_seed_passwords(monkeypatch):
    _prod_env(monkeypatch, BOOTSTRAP)
    plan = seed_plan_for_profile()
    # 用真实 store 与真实登录规则，只把全局 store 换成本用例私有实例
    from atlas.iam.accounts import UserStore
    from atlas.iam.sessions import SessionStore

    fresh = UserStore()
    fresh.bind_session_store(SessionStore())
    fresh.seed(plan)
    monkeypatch.setattr(deps, "user_store", fresh)
    monkeypatch.setattr(deps, "read_env_profile", lambda: "prod")

    principal = deps.authenticate_login(plan[0].username, BOOTSTRAP)
    assert (principal.tenant_id, principal.role) == (plan[0].tenant_id, Role.ADMIN)

    with pytest.raises(Exception) as seed_exc:
        deps.authenticate_login("admin-a", "admin123")
    assert getattr(seed_exc.value, "detail", None), "被拒也应给结构化原因"

    with pytest.raises(Exception):
        deps.authenticate_login("operator-a", BOOTSTRAP)  # prod 不播 operator


def test_u864_non_prod_plan_is_unchanged(monkeypatch):
    monkeypatch.setenv("ATLAS_ENV", "dev")
    assert seed_plan_for_profile() is SEED_USERS, "非 prod 必须原样返回，演示形态零变化"


# --- U865 幂等 ---------------------------------------------------------------


def test_u865_prod_seed_is_idempotent_and_keeps_rotated_password(monkeypatch):
    _prod_env(monkeypatch, BOOTSTRAP)
    from atlas.iam.accounts import UserStore

    store = UserStore()
    plan = seed_plan_for_profile()
    assert store.seed(plan) == len(plan)
    store.set_password(plan[0].tenant_id, plan[0].username, "Rotated-After-Install-1")
    assert store.seed(plan) == 0, "重复播种不新增行"
    from atlas.iam.passwords import verify_password

    rotated = store.get(plan[0].tenant_id, plan[0].username)
    assert verify_password("Rotated-After-Install-1", rotated.password_hash)
    assert not verify_password(BOOTSTRAP, rotated.password_hash), "重复播种不得把改过的口令抹回去"


# --- U866 反向门：把 fail-closed 短路，看会退回什么 ---------------------------


def test_u866_reverse_gate_shorting_the_refusal_reopens_the_plaintext_door(monkeypatch):
    """把"缺引导口令"改成"给个默认值"——正是 N1 修复要防的世界：明文口令重新被播进去。"""
    _prod_env(monkeypatch, None)
    monkeypatch.setattr(
        "atlas.security.bootstrap.prod_bootstrap_password",
        lambda: "admin123",
        raising=True,
    )
    plan = seed_plan_for_profile()
    assert any(u.password in {s.password for s in SEED_USERS} for u in plan), (
        "短路后应重新出现仓库明文口令；若仍不出现，说明本用例守的不是同一件事"
    )


# --- U867 示例配置自身合法 ----------------------------------------------------


def test_u867_env_example_values_are_valid():
    raw = Path(".env.example").read_text(encoding="utf-8")
    match = re.search(r"^ATLAS_ENV=(\S+)$", raw, re.M)
    assert match, ".env.example 必须给出 ATLAS_ENV"
    assert match.group(1) in {"dev", "test", "prod"}, (
        f"示例值 {match.group(1)!r} 会被 read_env_profile() 拒绝（docs/63 N5）"
    )
    assert PROD_BOOTSTRAP_PASSWORD_ENV in raw, "prod 必需项必须出现在示例里"


def test_weak_list_guard_sanity():
    """U861 的前提：`password` 确实在黑名单里，否则那条测的是别的东西。"""
    assert "password" in WEAK_PASSWORDS
