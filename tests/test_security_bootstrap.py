# -*- coding: utf-8 -*-
"""J-1a：ATLAS_ENV fail-closed 环境门（docs/64 打包 J）。

覆盖：bootstrap 纯函数 3 测（缺省/非法/prod 密钥门）+ email_token/oauth
两 resolve 在 prod 下缺密钥 raise（fail-closed）、非 prod 行为不变。
"""

from __future__ import annotations

import pytest

from atlas.security.bootstrap import assert_prod_secrets, read_env_profile


def _unset_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_ENV", raising=False)
    monkeypatch.delenv("ATLAS_APPROVAL_HMAC_SECRET", raising=False)
    monkeypatch.delenv("ATLAS_MASTER_KEY", raising=False)


def test_read_env_profile_defaults_to_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    _unset_all(monkeypatch)
    assert read_env_profile() == "dev"


def test_read_env_profile_rejects_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_ENV", "staging")
    with pytest.raises(ValueError):
        read_env_profile()


def test_assert_prod_secrets_raises_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_APPROVAL_HMAC_SECRET", "x" * 32)
    monkeypatch.delenv("ATLAS_MASTER_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ATLAS_ENV=prod"):
        assert_prod_secrets()


def test_assert_prod_secrets_rejects_short_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_APPROVAL_HMAC_SECRET", "short")
    monkeypatch.setenv("ATLAS_MASTER_KEY", "x" * 32)
    with pytest.raises(RuntimeError):
        assert_prod_secrets()


def test_assert_prod_secrets_passes_with_both_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_APPROVAL_HMAC_SECRET", "a" * 32)
    monkeypatch.setenv("ATLAS_MASTER_KEY", "b" * 32)
    # docs/66 打包 L 改了本条的前提：prod 除了两把密钥还要引导口令，缺它即拒启
    # （原行为由 test_u861b_secret_gate_also_requires_the_bootstrap_value 反向钉住）。
    monkeypatch.setenv("ATLAS_ADMIN_BOOTSTRAP_PASSWORD", "Boot-Strap-2026")
    assert_prod_secrets()  # 不 raise


def test_assert_prod_secrets_passes_in_non_prod(monkeypatch: pytest.MonkeyPatch) -> None:
    _unset_all(monkeypatch)
    assert_prod_secrets()  # dev 缺密钥静默通过


def test_email_token_resolve_raises_in_prod_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from atlas.collaboration import email_token

    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_APPROVAL_HMAC_SECRET", raising=False)
    monkeypatch.delenv("ATLAS_MASTER_KEY", raising=False)
    with pytest.raises(RuntimeError, match="dev 密钥"):
        email_token.resolve_token_secret()


def test_email_token_resolve_uses_dev_key_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    from atlas.collaboration import email_token

    _unset_all(monkeypatch)
    assert email_token.resolve_token_secret() == email_token._DEV_TOKEN_SECRET


def test_oauth_resolve_raises_in_prod_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from atlas.connections import oauth

    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_MASTER_KEY", raising=False)
    with pytest.raises(RuntimeError, match="dev 密钥"):
        oauth.resolve_state_secret()


def test_oauth_resolve_uses_dev_key_in_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    from atlas.connections import oauth

    monkeypatch.delenv("ATLAS_ENV", raising=False)
    monkeypatch.delenv("ATLAS_MASTER_KEY", raising=False)
    assert oauth.resolve_state_secret() == oauth._DEV_STATE_SECRET
