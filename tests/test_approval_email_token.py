# -*- coding: utf-8 -*-
"""邮件一键决策 capability token 测试（docs/36 §3）。

覆盖：TokenIssuer 签发/验签往返、载荷形状与 TTL 裁剪（含 3600 上限与
+300 grace）、分段/载荷畸形、签名篡改、过期、错误密钥验签；
resolve_token_secret 的环境优先级与 dev fallback warning。
"""

from __future__ import annotations

import logging

import pytest

from atlas.collaboration.email_token import (
    EXPIRY_GRACE_SECONDS,
    MAX_TTL_SECONDS,
    TokenBadSignature,
    TokenExpired,
    TokenIssuer,
    TokenMalformed,
    resolve_token_secret,
)

DEV_SECRET = "atlas-dev-approval-email-insecure-do-not-use-in-prod"


class FakeClock:
    def __init__(self, value: float = 1_000_000.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def _decode_payload(token: str) -> dict:
    import base64
    import json

    payload_b64 = token.split(".", 1)[0]
    raw = base64.urlsafe_b64decode(payload_b64 + "=" * (-len(payload_b64) % 4))
    return json.loads(raw)


# ============================ 签发/验签往返 ============================


def test_issue_and_verify_roundtrip():
    clock = FakeClock()
    issuer = TokenIssuer(secret="s3cret", clock=clock)
    token = issuer.issue("t1", "approval-tok", 120)
    body = issuer.verify(token)
    assert body["v"] == 1
    assert body["tenant"] == "t1"
    assert body["at"] == "approval-tok"
    assert body["iat"] == 1_000_000
    assert body["exp"] == 1_000_120 + EXPIRY_GRACE_SECONDS


def test_token_has_two_segments_and_compact_json():
    issuer = TokenIssuer(secret="s", clock=FakeClock())
    token = issuer.issue("t", "a", 10)
    assert token.count(".") == 1
    payload_b64 = token.split(".", 1)[0]
    assert " " not in payload_b64


def test_empty_tenant_and_empty_approval_token_roundtrip():
    issuer = TokenIssuer(secret="s", clock=FakeClock())
    token = issuer.issue("", "", 10)
    body = issuer.verify(token)
    assert body["tenant"] == ""
    assert body["at"] == ""


def test_ttl_capped_at_max_plus_grace():
    issuer = TokenIssuer(secret="s", clock=FakeClock())
    token = issuer.issue("t", "a", MAX_TTL_SECONDS * 10)
    body = _decode_payload(token)
    assert body["exp"] - body["iat"] == MAX_TTL_SECONDS + EXPIRY_GRACE_SECONDS


def test_ttl_fractional_timeout_truncated():
    issuer = TokenIssuer(secret="s", clock=FakeClock())
    token = issuer.issue("t", "a", 42.9)
    body = _decode_payload(token)
    assert body["exp"] - body["iat"] == 42 + EXPIRY_GRACE_SECONDS


# ============================ 验签失败 ============================


def test_verify_rejects_wrong_secret():
    clock = FakeClock()
    token = TokenIssuer(secret="a", clock=clock).issue("t", "a", 10)
    with pytest.raises(TokenBadSignature):
        TokenIssuer(secret="b", clock=clock).verify(token)


def test_verify_rejects_tampered_payload():
    issuer = TokenIssuer(secret="s", clock=FakeClock())
    token = issuer.issue("t", "a", 10)
    payload_b64, sig = token.split(".", 1)
    with pytest.raises(TokenBadSignature):
        issuer.verify(payload_b64 + "x." + sig)


def test_verify_rejects_tampered_signature():
    issuer = TokenIssuer(secret="s", clock=FakeClock())
    token = issuer.issue("t", "a", 10)
    with pytest.raises(TokenBadSignature):
        issuer.verify(token + "x")


def test_verify_rejects_malformed_segments():
    issuer = TokenIssuer(secret="s", clock=FakeClock())
    for bad in ["", "nodot", "a.b.c"]:
        with pytest.raises(TokenMalformed):
            issuer.verify(bad)


def test_verify_rejects_empty_segment_as_bad_signature():
    issuer = TokenIssuer(secret="s", clock=FakeClock())
    for bad in [".", "abc."]:
        with pytest.raises(TokenBadSignature):
            issuer.verify(bad)


def test_verify_rejects_unparseable_payload():
    import base64

    issuer = TokenIssuer(secret="s", clock=FakeClock())
    bad_payload = base64.urlsafe_b64encode(b"not-json").rstrip(b"=").decode()
    token = f"{bad_payload}.{_sign(bad_payload, 's')}"
    with pytest.raises(TokenMalformed):
        issuer.verify(token)


def _sign(payload_b64: str, secret: str) -> str:
    import base64
    import hashlib
    import hmac

    digest = hmac.new(secret.encode(), payload_b64.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def test_verify_expired_raises_token_expired():
    clock = FakeClock(1_000_000.0)
    issuer = TokenIssuer(secret="s", clock=clock)
    token = issuer.issue("t", "a", 10)  # exp = 1_000_310
    clock.value = 1_000_311.0
    with pytest.raises(TokenExpired):
        issuer.verify(token)


def test_verify_accepts_at_exact_expiry():
    clock = FakeClock(1_000_000.0)
    issuer = TokenIssuer(secret="s", clock=clock)
    token = issuer.issue("t", "a", 10)
    clock.value = 1_000_310.0 - 1
    assert issuer.verify(token)["exp"] == 1_000_310


# ============================ resolve_token_secret ============================


def test_resolve_secret_prefers_explicit_env(monkeypatch):
    monkeypatch.setenv("ATLAS_APPROVAL_HMAC_SECRET", "explicit-key")
    monkeypatch.setenv("ATLAS_MASTER_KEY", "master-key")
    assert resolve_token_secret() == "explicit-key"


def test_resolve_secret_falls_back_to_master_key(monkeypatch):
    monkeypatch.delenv("ATLAS_APPROVAL_HMAC_SECRET", raising=False)
    monkeypatch.setenv("ATLAS_MASTER_KEY", "master-key")
    assert resolve_token_secret() == "master-key"


def test_resolve_secret_uses_dev_fallback_and_warns(monkeypatch, caplog):
    monkeypatch.delenv("ATLAS_APPROVAL_HMAC_SECRET", raising=False)
    monkeypatch.delenv("ATLAS_MASTER_KEY", raising=False)
    resolve_token_secret._warned = False
    with caplog.at_level(logging.WARNING):
        secret = resolve_token_secret()
    assert secret == DEV_SECRET
    assert any("ATLAS_APPROVAL_HMAC_SECRET" in r.message for r in caplog.records)
