"""凭证信封 / SecretProvider / secret:// 解析 / 全链路脱敏测试（docs/32 §2，U233/U234）。"""

from __future__ import annotations

import base64
import json
import logging

import httpx
import pytest

from atlas.httpapi.resilience import RetryPolicy
from atlas.httpapi.service import HttpApiCallError, HttpApiClient
from atlas.security.secrets import (
    AesGcmSecretProvider,
    PlaintextSecretProvider,
    SecretDecryptError,
    SecretUnavailable,
    build_secret_provider_from_env,
    redact_headers,
    resolve_references,
)

PUBLIC_RESOLVER = lambda _host: ["93.184.216.34"]
NOOP_RETRY = RetryPolicy(sleep=lambda _: None)


# --- U233 纯逻辑：plain provider / 引用解析 / 脱敏 ---

def test_plain_provider_roundtrip():
    provider = PlaintextSecretProvider()
    envelope = provider.encrypt("hello")
    assert envelope.startswith("enc$v1$plain$")
    assert provider.decrypt(envelope) == "hello"


def test_get_secret_returns_plaintext_and_envelope():
    provider = PlaintextSecretProvider(
        {"token": "raw-tok", "wrapped": PlaintextSecretProvider().encrypt("env-tok")}
    )
    assert provider.get_secret("token") == "raw-tok"
    assert provider.get_secret("wrapped") == "env-tok"


def test_get_secret_missing_raises():
    with pytest.raises(SecretUnavailable):
        PlaintextSecretProvider().get_secret("nope")


def test_plain_provider_cannot_decrypt_aes_envelope():
    aes = pytest.importorskip("cryptography")  # 仅为构造一个 AES 信封
    key = b"0" * 32
    aes_provider = AesGcmSecretProvider(key, kid="k1")
    envelope = aes_provider.encrypt("x")
    with pytest.raises(SecretDecryptError):
        PlaintextSecretProvider().decrypt(envelope)


def test_resolve_secret_reference_in_header():
    provider = PlaintextSecretProvider({"t": "tok-123"})
    assert resolve_references("Bearer secret://t", provider) == "Bearer tok-123"
    assert resolve_references("secret://t", provider) == "tok-123"


def test_resolve_envelope_in_string():
    provider = PlaintextSecretProvider()
    envelope = provider.encrypt("pw")
    assert resolve_references(envelope, provider) == "pw"


def test_resolve_without_provider_is_fail_closed():
    with pytest.raises(SecretUnavailable):
        resolve_references("Bearer secret://t", None)
    with pytest.raises(SecretUnavailable):
        resolve_references(PlaintextSecretProvider().encrypt("x"), None)
    # 无引用的普通字符串不需要 provider
    assert resolve_references("ordinary", None) == "ordinary"
    assert resolve_references(123, None) == 123


def test_redact_headers_masks_sensitive_and_refs():
    headers = {
        "Authorization": "Bearer tok",
        "proxy-authorization": "Basic x",
        "Cookie": "sid=1",
        "Set-Cookie": "sid=2",
        "X-Token": "secret://t",
        "X-Env": PlaintextSecretProvider().encrypt("y"),
        "Content-Type": "application/json",
        "X-Trace": "abc",
    }
    redacted = redact_headers(headers)
    assert redacted["Authorization"] == "***"
    assert redacted["proxy-authorization"] == "***"
    assert redacted["Cookie"] == "***"
    assert redacted["Set-Cookie"] == "***"
    assert redacted["X-Token"] == "***"
    assert redacted["X-Env"] == "***"
    assert redacted["Content-Type"] == "application/json"
    assert redacted["X-Trace"] == "abc"


# --- U233 全链路：秘密只在请求里出现，不进观测/记录/异常 ---

def _capturing_client(secrets, captured):
    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["url"] = str(request.url)
        return httpx.Response(200, json={"ok": True}, request=request)

    provider = PlaintextSecretProvider(secrets)
    client = HttpApiClient(
        base_url="http://api.example.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=PUBLIC_RESOLVER,
        retry=NOOP_RETRY,
        secret_provider=provider,
    )
    return client


def test_secret_header_sent_but_never_recorded():
    captured: dict = {}
    client = _capturing_client({"api_token": "tok-secret-123"}, captured)

    out = client.request("GET", "/ping", headers={"Authorization": "Bearer secret://api_token"})

    assert out["status"] == 200
    # 请求确实带上了真实凭证
    assert captured["authorization"] == "Bearer tok-secret-123"
    # last_request 不含请求头、不含明文秘密
    assert "headers" not in client.last_request
    assert "tok-secret-123" not in json.dumps(client.last_request, ensure_ascii=False)
    # 适配器 observe 观测通道同样不含明文秘密
    from atlas.httpapi.adapter import HttpApiHarnessAdapter

    adapter = HttpApiHarnessAdapter(client=client, granted_permissions={"read", "write"})
    observed = adapter.observe()
    assert "tok-secret-123" not in json.dumps(observed.data, ensure_ascii=False, default=str)


def test_secret_url_resolved_and_sent():
    captured: dict = {}
    client = _capturing_client({"hook": "http://api.example.com/hook"}, captured)

    client.request("POST", "secret://hook", body={"x": 1})

    assert captured["url"] == "http://api.example.com/hook"


def test_missing_secret_fails_closed_without_request():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200)

    client = HttpApiClient(
        base_url="http://api.example.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=PUBLIC_RESOLVER,
        retry=NOOP_RETRY,
        secret_provider=PlaintextSecretProvider({"present": "ok"}),
    )
    with pytest.raises(HttpApiCallError) as exc_info:
        client.request("GET", "/x", headers={"Authorization": "Bearer secret://missing"})
    assert exc_info.value.code == "SECRET_UNAVAILABLE"
    # 错误消息只含名字，不含任何秘密值
    assert "ok" not in str(exc_info.value)
    assert calls["n"] == 0  # 零外呼
    assert client.last_request is None  # 不写记录


def test_response_set_cookie_redacted_in_output():
    def handler(request):
        return httpx.Response(200, headers={"Set-Cookie": "sid=secret-session; Path=/"}, json={"ok": True})

    client = HttpApiClient(
        base_url="http://api.example.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=PUBLIC_RESOLVER,
        retry=NOOP_RETRY,
        secret_provider=PlaintextSecretProvider(),
    )
    out = client.request("GET", "/x")
    assert out["headers"].get("set-cookie") == "***"
    assert "secret-session" not in json.dumps(out)


# --- U234 AES-GCM（选 a：cryptography） ---

cryptography = pytest.importorskip("cryptography")


def _key() -> bytes:
    return b"0" * 32


def test_aes_roundtrip():
    provider = AesGcmSecretProvider(_key(), kid="k1")
    envelope = provider.encrypt("中文秘密 token 🎫")
    assert envelope.startswith("enc$v1$k1$")
    assert provider.decrypt(envelope) == "中文秘密 token 🎫"


def test_aes_iv_randomized_ciphertext_differs():
    provider = AesGcmSecretProvider(_key())
    e1 = provider.encrypt("same")
    e2 = provider.encrypt("same")
    assert e1 != e2
    assert provider.decrypt(e1) == provider.decrypt(e2) == "same"


def test_aes_tampered_ciphertext_rejected():
    provider = AesGcmSecretProvider(_key())
    envelope = provider.encrypt("secret")
    head, _, ct = envelope.rpartition("$")
    tampered = head + "$" + ("A" if ct[0] != "A" else "B") + ct[1:]
    with pytest.raises(SecretDecryptError):
        provider.decrypt(tampered)


def test_aes_wrong_key_rejected():
    encrypter = AesGcmSecretProvider(b"1" * 32, kid="k1")
    decrypter = AesGcmSecretProvider(b"2" * 32, kid="k1")
    envelope = encrypter.encrypt("x")
    with pytest.raises(SecretDecryptError):
        decrypter.decrypt(envelope)


def test_aes_wrong_kid_aad_rejected():
    encrypter = AesGcmSecretProvider(_key(), kid="k1")
    other_kid = AesGcmSecretProvider(_key(), kid="k2")
    envelope = encrypter.encrypt("x")
    with pytest.raises(SecretDecryptError):
        other_kid.decrypt(envelope)


def test_aes_provider_rejects_plain_envelope():
    plain_env = PlaintextSecretProvider().encrypt("x")
    with pytest.raises(SecretDecryptError):
        AesGcmSecretProvider(_key()).decrypt(plain_env)


def test_aes_get_secret_reads_encrypted_value():
    provider = AesGcmSecretProvider(_key(), kid="k1", secrets={"t": AesGcmSecretProvider(_key()).encrypt("deep")})
    assert provider.get_secret("t") == "deep"


def test_aes_end_to_end_header_via_client():
    captured: dict = {}

    def handler(request):
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json={})

    provider = AesGcmSecretProvider(_key(), kid="k1")
    envelope = provider.encrypt("deep-token")
    client = HttpApiClient(
        base_url="http://api.example.com",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        resolver=PUBLIC_RESOLVER,
        retry=NOOP_RETRY,
        secret_provider=provider,
    )
    client.request("GET", "/x", headers={"Authorization": f"Bearer {envelope}"})
    assert captured["authorization"] == "Bearer deep-token"


def test_build_from_env_plaintext_when_no_master_key(monkeypatch, caplog):
    monkeypatch.delenv("ATLAS_MASTER_KEY", raising=False)
    monkeypatch.delenv("ATLAS_SECRETS", raising=False)
    with caplog.at_level(logging.WARNING):
        provider = build_secret_provider_from_env()
    assert isinstance(provider, PlaintextSecretProvider)
    assert any("ATLAS_MASTER_KEY" in r.message for r in caplog.records)


def test_build_from_env_aes_when_master_key(monkeypatch):
    key = base64.urlsafe_b64encode(_key()).decode().rstrip("=")
    monkeypatch.setenv("ATLAS_MASTER_KEY", key)
    monkeypatch.setenv("ATLAS_MASTER_KEY_ID", "k1")
    provider = build_secret_provider_from_env()
    assert isinstance(provider, AesGcmSecretProvider)
    assert provider.decrypt(provider.encrypt("x")) == "x"


def test_build_from_env_fail_closed_on_bad_master_key(monkeypatch):
    monkeypatch.setenv("ATLAS_MASTER_KEY", base64.urlsafe_b64encode(b"short").decode())
    with pytest.raises(RuntimeError):
        build_secret_provider_from_env()


def test_build_from_env_rejects_bad_secrets_json(monkeypatch):
    monkeypatch.delenv("ATLAS_MASTER_KEY", raising=False)
    monkeypatch.setenv("ATLAS_SECRETS", "{not-json")
    with pytest.raises(ValueError):
        build_secret_provider_from_env()


def test_build_from_env_injects_named_secrets(monkeypatch):
    monkeypatch.delenv("ATLAS_MASTER_KEY", raising=False)
    monkeypatch.setenv("ATLAS_SECRETS", json.dumps({"t": "tv"}))
    provider = build_secret_provider_from_env()
    assert provider.get_secret("t") == "tv"
