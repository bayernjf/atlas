"""W5-3 验收：model-hub 投影同步、信封加密与统一解析收口。

覆盖：
- hub_settings：未配置 / kill switch / 配置三态；
- sync_hub_projection：拉取 → api_key 用 atlas 信封加密 → 落库零明文；
- litellm 映射：litellm_id 优先，否则原始 model；
- resolve_hub_model：默认（首个 primary）/ alias / 模态；
- resolve_runtime_model：BYOK > hub > env 优先级与空投影回退。
"""

from __future__ import annotations

import pytest

from atlas.llm import hub
from atlas.llm.config import ModelConfig

# 复用既有 patch 方式
from tests.test_model_config import _patch_secret_provider


class _FakeSecretProvider:
    """信封桩：token 带计数，保证不同明文不冲突；encrypt/decrypt 往返。"""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}
        self._counter = 0

    def encrypt(self, plaintext: str) -> str:
        self._counter += 1
        token = f"enc$v1${self._counter}${len(plaintext)}"
        self._store[token] = plaintext
        return token

    def decrypt(self, token: str) -> str:
        if token not in self._store:
            raise ValueError(f"bad envelope: {token}")
        return self._store[token]


# ---------------- fake hub client ----------------


class FakeHubClient:
    def __init__(self, models, revision=42) -> None:
        self._models = models
        self._revision = revision
        self.pull_count = 0

    def pull(self) -> None:
        self.pull_count += 1

    def list(self, modality: str | None = None):
        if modality is None:
            return list(self._models)
        return [m for m in self._models if modality in m.get("modalities", [])]

    @property
    def revision(self):
        return self._revision


def _text_model(
    assignment_id: str,
    alias: str | None,
    model: str,
    *,
    litellm_id: str | None = None,
    role: str = "primary",
    modalities: list[str] | None = None,
    key: str = "sk-KEY",
) -> dict:
    return {
        "assignment_id": assignment_id,
        "alias": alias,
        "role": role,
        "modalities": modalities or ["text"],
        "model": model,
        "litellm_id": litellm_id,
        "base_url": "https://gateway.example/v1",
        "api_key": key,
    }


@pytest.fixture(autouse=True)
def _reset_projection():
    hub.reset_hub_projection()
    yield
    hub.reset_hub_projection()


# ---------------- settings ----------------


def test_hub_settings_unconfigured(monkeypatch):
    monkeypatch.delenv("MODEL_HUB_URL", raising=False)
    monkeypatch.delenv("MODEL_HUB_TOKEN", raising=False)
    assert hub.hub_settings() is None
    assert hub.is_hub_configured() is False
    assert hub.get_hub_client() is None


def test_hub_settings_disabled(monkeypatch):
    monkeypatch.setenv("MODEL_HUB_URL", "http://hub:8791")
    monkeypatch.setenv("MODEL_HUB_TOKEN", "tok_x")
    monkeypatch.setenv("MODEL_HUB_DISABLED", "1")
    assert hub.hub_settings() is None
    assert hub.is_hub_configured() is False


def test_hub_settings_configured(monkeypatch):
    monkeypatch.setenv("MODEL_HUB_URL", "http://hub:8791/")
    monkeypatch.setenv("MODEL_HUB_TOKEN", "tok_x")
    monkeypatch.delenv("MODEL_HUB_DISABLED", raising=False)
    settings = hub.hub_settings()
    assert settings is not None
    assert settings[0] == "http://hub:8791"
    assert settings[1] == "tok_x"
    assert settings[2] == hub.DEFAULT_CACHE_PATH
    assert hub.is_hub_configured() is True


# ---------------- sync ----------------


def test_sync_encrypts_and_persists(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    models = [
        _text_model("asn_1", "noul", "ark-code-latest", key="sk-ark-PLAN"),
        _text_model(
            "asn_2", "score", "doubao-raw",
            litellm_id="openai/doubao-mapped", key="sk-ark-PAYG",
        ),
    ]
    client = FakeHubClient(models)
    result = hub.sync_hub_projection(client)
    assert result == {"revision": 42, "pulled": 2}
    assert client.pull_count == 1

    projection = hub.load_projection()
    assert projection is not None
    assert projection.revision == 42
    # 落库零明文：整个投影序列化不含任何真实 key
    import json
    from dataclasses import asdict

    blob = json.dumps(asdict(projection))
    assert "sk-ark-PLAN" not in blob
    assert "sk-ark-PAYG" not in blob


def test_sync_litellm_mapping(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    models = [
        _text_model("asn_1", "noul", "ark-code-latest", litellm_id=None),
        _text_model("asn_2", "score", "doubao-raw", litellm_id="openai/doubao-mapped"),
    ]
    hub.sync_hub_projection(FakeHubClient(models))
    projection = hub.load_projection()
    assert projection is not None
    by_id = {m.assignment_id: m for m in projection.models}
    # 无 litellm_id → 原始 model
    assert by_id["asn_1"].model == "ark-code-latest"
    assert by_id["asn_1"].raw_model == "ark-code-latest"
    # 有 litellm_id → 映射名
    assert by_id["asn_2"].model == "openai/doubao-mapped"
    assert by_id["asn_2"].raw_model == "doubao-raw"


def test_sync_first_primary_is_default(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    models = [
        _text_model("asn_1", "noul", "m1", role="primary"),
        _text_model("asn_2", "score", "m2", role="primary"),
    ]
    hub.sync_hub_projection(FakeHubClient(models))
    projection = hub.load_projection()
    assert projection is not None
    defaults = [m for m in projection.models if m.is_default]
    assert len(defaults) == 1
    assert defaults[0].assignment_id == "asn_1"


# ---------------- resolve ----------------


def test_resolve_default_text_model(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    models = [
        _text_model("asn_1", "noul", "ark-code-latest", key="sk-plan"),
        _text_model("asn_2", "score", "doubao-raw",
                    litellm_id="openai/doubao", key="sk-payg"),
    ]
    hub.sync_hub_projection(FakeHubClient(models))
    resolved = hub.resolve_hub_model()
    assert resolved is not None
    # 首个 primary 即默认
    assert resolved.model == "ark-code-latest"
    assert resolved.api_key == "sk-plan"
    assert resolved.assignment_id == "asn_1"
    assert resolved.base_url == "https://gateway.example/v1"


def test_resolve_by_alias(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    models = [
        _text_model("asn_1", "noul", "ark-code-latest", key="sk-plan"),
        _text_model("asn_2", "score", "doubao-raw",
                    litellm_id="openai/doubao", key="sk-payg"),
    ]
    hub.sync_hub_projection(FakeHubClient(models))
    resolved = hub.resolve_hub_model(alias="score")
    assert resolved is not None
    assert resolved.model == "openai/doubao"
    assert resolved.api_key == "sk-payg"
    assert resolved.assignment_id == "asn_2"


def test_resolve_by_modality(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    models = [
        _text_model("asn_1", "noul", "text-model", key="sk-text"),
        _text_model("asn_2", None, "image-model", litellm_id="openai/image",
                    modalities=["image"], key="sk-image"),
    ]
    hub.sync_hub_projection(FakeHubClient(models))
    resolved = hub.resolve_hub_model(modality="image")
    assert resolved is not None
    assert resolved.model == "openai/image"
    assert resolved.api_key == "sk-image"


def test_resolve_no_projection_returns_none():
    assert hub.resolve_hub_model() is None


def test_resolve_model_without_key_returns_none(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    models = [_text_model("asn_1", "noul", "m", key="")]
    hub.sync_hub_projection(FakeHubClient(models))
    assert hub.resolve_hub_model() is None


# ---------------- resolve_runtime_model 优先级 ----------------


def test_runtime_byok_wins_over_hub(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    hub.sync_hub_projection(
        FakeHubClient([_text_model("asn_1", "noul", "hub-model", key="sk-hub")])
    )
    import atlas.llm.config as config_mod

    monkeypatch.setattr(config_mod, "_model_config_store", None)
    store = config_mod.get_model_config_store()
    store.set_byok(
        "tenant-a",
        ModelConfig(
            mode="byok", model="openai/byok-model",
            api_key_enc=provider.encrypt("sk-byok"),
            base_url="https://byok.example/v1",
        ),
    )
    rt = hub.resolve_runtime_model("tenant-a")
    assert rt is not None
    assert rt.source == "byok"
    assert rt.model == "openai/byok-model"
    assert rt.api_key == "sk-byok"


def test_runtime_falls_back_to_hub(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    hub.sync_hub_projection(
        FakeHubClient([_text_model("asn_1", "noul", "hub-model", key="sk-hub")])
    )
    monkeypatch.setattr(
        "atlas.llm.config._model_config_store", None
    )
    # 无 BYOK（tenant 存在但无配置）→ hub
    rt = hub.resolve_runtime_model("tenant-a")
    assert rt is not None
    assert rt.source == "hub"
    assert rt.model == "hub-model"
    assert rt.api_key == "sk-hub"
    assert rt.assignment_id == "asn_1"


def test_runtime_falls_back_to_env(monkeypatch):
    monkeypatch.setattr(
        "atlas.llm.config._model_config_store", None
    )
    monkeypatch.setenv("LITELLM_MODEL", "openai/env-model")
    rt = hub.resolve_runtime_model("tenant-a")
    assert rt is not None
    assert rt.source == "env"
    assert rt.model == "openai/env-model"
    assert rt.api_key is None


def test_runtime_none_when_empty(monkeypatch):
    monkeypatch.setattr(
        "atlas.llm.config._model_config_store", None
    )
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    assert hub.resolve_runtime_model("tenant-a") is None


# ---------------- 后台刷新 ----------------


def test_refresh_not_started_when_unconfigured(monkeypatch):
    monkeypatch.delenv("MODEL_HUB_URL", raising=False)
    monkeypatch.delenv("MODEL_HUB_TOKEN", raising=False)
    hub.start_hub_refresh()
    assert hub._refresh_thread is None


def test_refresh_start_and_stop(monkeypatch):
    monkeypatch.setenv("MODEL_HUB_URL", "http://hub:8791")
    monkeypatch.setenv("MODEL_HUB_TOKEN", "tok_x")
    # 让刷新间隔很长，避免测试中真正触发
    monkeypatch.setattr(hub, "REFRESH_INTERVAL_SECONDS", 3600.0)
    hub.start_hub_refresh()
    assert hub._refresh_thread is not None
    assert hub._refresh_thread.is_alive()
    hub.stop_hub_refresh()
    assert hub._refresh_thread is None


# ---------------- report_usage ----------------


class _FakeResp:
    def read(self):
        return b"{}"


def test_report_usage_posts(monkeypatch):
    import json as _json

    monkeypatch.setenv("MODEL_HUB_URL", "http://hub:8791")
    monkeypatch.setenv("MODEL_HUB_TOKEN", "tok_x")
    captured: dict = {}

    def opener(req):
        captured["url"] = req.full_url
        captured["body"] = _json.loads(req.data.decode())
        captured["headers"] = dict(req.header_items())
        return _FakeResp()

    ok = hub.report_usage(
        assignment_id="asn_1",
        model="ark-code-latest",
        provider="ark",
        feature="ai_decision",
        input_tokens=10,
        output_tokens=5,
        billing_source="builtin",
        opener=opener,
    )
    assert ok
    assert captured["url"] == "http://hub:8791/pub/v1/usage"
    assert captured["body"]["assignment_id"] == "asn_1"
    assert captured["body"]["model"] == "ark-code-latest"
    assert captured["body"]["billing_source"] == "builtin"
    assert captured["body"]["project"] == "atlas"
    # Bearer token 随请求（header_items 的 key 被 capitalize，统一小写比较）
    auth = [v for k, v in captured["headers"].items() if k.lower() == "authorization"]
    assert auth == ["Bearer tok_x"]


def test_report_usage_skips_byok(monkeypatch):
    monkeypatch.setenv("MODEL_HUB_URL", "http://hub:8791")
    monkeypatch.setenv("MODEL_HUB_TOKEN", "tok_x")

    def opener(req):
        raise AssertionError("BYOK must not be reported")

    assert not hub.report_usage(
        assignment_id="asn_1",
        model="m",
        provider="ark",
        feature="f",
        input_tokens=1,
        output_tokens=1,
        billing_source="byok",
        opener=opener,
    )


def test_report_usage_skips_without_assignment(monkeypatch):
    monkeypatch.setenv("MODEL_HUB_URL", "http://hub:8791")
    monkeypatch.setenv("MODEL_HUB_TOKEN", "tok_x")
    assert not hub.report_usage(
        assignment_id="",
        model="m",
        provider="ark",
        feature="f",
        input_tokens=1,
        output_tokens=1,
    )
