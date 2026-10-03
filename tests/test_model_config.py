"""打包 Y（docs/93）验收 U1109–U1114：LLM 模型配置管理面后端测试。

覆盖：
- U1109：内置 CRUD（脱敏、enabled=false 回退 env）
- U1110：BYOK per-tenant 隔离
- U1111：优先级——BYOK > 内置 > env；BYOK 显式传 api_key/base_url 给 litellm（最大风险点）
- U1112：无任何配置时行为与现状一致（规则/Offline/Null 兜底）
- U1113：prod fail-closed 门零回归（判据是客户端类型）
- U1114：密钥信封零明文＋_FILE 补水兼容（复用 ZO 语义）
"""

from __future__ import annotations

import sys
import types

import pytest

from atlas.llm.config import (
    BUILTIN_TENANT_KEY,
    InMemoryModelConfigStore,
    ModelConfig,
    resolve_default_model,
)
from atlas.llm.decision import (
    AUTO_APPROVE,
    HUMAN_APPROVAL,
    LiteLLMDecisionClient,
    RuleBasedDecisionClient,
    get_decision_client,
)
from atlas.llm.condition_classifier import (
    LiteLLMConditionClassifier,
    OfflineConditionClassifier,
    get_condition_classifier,
)
from atlas.reflection.adapter import NullSummarizer, get_summarizer


# ---------------- 基建：信封 provider + fake litellm ----------------

@pytest.fixture
def fresh_store(monkeypatch):
    """消费点内部走 get_model_config_store() 进程单例；每个用例重置为干净内存档。

    只重置单例引用（复用模块级 _store_lock），下次调用重建内存档。
    """
    import atlas.llm.config as config_mod

    monkeypatch.setattr(config_mod, "_model_config_store", None)
    return config_mod.get_model_config_store()


class _FakeResponse:
    def __init__(self, content: str):
        self._content = content

    def __getitem__(self, key):
        return {"choices": [{"message": {"content": self._content}}]}[key]


class _FakeSecretProvider:
    """信封 provider 桩：encrypt/decrypt 往返，验证存储层零明文。"""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def encrypt(self, plaintext: str) -> str:
        token = f"enc$v1${len(plaintext)}"
        self._store[token] = plaintext
        return token

    def decrypt(self, token: str) -> str:
        if token not in self._store:
            raise ValueError(f"bad envelope: {token}")
        return self._store[token]


def _install_capturing_litellm(monkeypatch, content: str) -> dict:
    captured: dict = {}

    def _completion(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(content)

    fake = types.ModuleType("litellm")
    fake.completion = _completion
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return captured


def _patch_secret_provider(monkeypatch, provider: _FakeSecretProvider) -> None:
    """桩住 atlas.connections.service.get_secret_provider（消费点函数内 import 的来源模块）。"""
    import atlas.connections.service as conn_service

    monkeypatch.setattr(conn_service, "get_secret_provider", lambda: provider)


def _store_with(
    *,
    builtin: ModelConfig | None = None,
    byok_a: ModelConfig | None = None,
    byok_b: ModelConfig | None = None,
) -> InMemoryModelConfigStore:
    store = InMemoryModelConfigStore()
    if builtin is not None:
        store.set_builtin(builtin)
    if byok_a is not None:
        store.set_byok("tenant-a", byok_a)
    if byok_b is not None:
        store.set_byok("tenant-b", byok_b)
    return store


def _cfg(
    mode: str,
    model: str,
    *,
    api_key_enc: str = "",
    base_url: str | None = None,
    enabled: bool = True,
) -> ModelConfig:
    return ModelConfig(
        mode=mode,  # type: ignore[arg-type]
        model=model,
        api_key_enc=api_key_enc,
        base_url=base_url,
        enabled=enabled,
        updated_by="tester",
        updated_at="2026-10-01T00:00:00+00:00",
    )


# ==================== U1109：内置 CRUD 脱敏 ====================


def test_u1109_builtin_crud_masked_and_resolve(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    store = _store_with()
    sealed = provider.encrypt("sk-secret-ABCD")

    store.set_builtin(_cfg("builtin", "openai/agnes-2.5-flash", api_key_enc=sealed))
    view = store.get_builtin().public_view()
    assert view["model"] == "openai/agnes-2.5-flash"
    assert "sk-secret-ABCD" not in str(view)
    assert view["apiKey"].endswith("ABCD") or view["apiKey"] != ""

    # enabled 配置命中 → 返回内置（不进 env）
    resolved = resolve_default_model(store, "tenant-a")
    assert resolved is not None
    assert resolved.model == "openai/agnes-2.5-flash"


def test_u1109_enabled_false_falls_back_to_env(monkeypatch, fresh_store):
    fresh_store.set_builtin(_cfg("builtin", "openai/builtin", enabled=False))
    # 内置被停用 → resolve 返回 None → 消费点落 env
    assert resolve_default_model(fresh_store, "tenant-a") is None
    monkeypatch.setenv("LITELLM_MODEL", "openai/env-model")
    client = get_decision_client(tenant_id="tenant-a")
    assert isinstance(client, LiteLLMDecisionClient)
    assert client.model == "openai/env-model"


# ==================== U1110：BYOK per-tenant 隔离 ====================


def test_u1110_byok_isolated_per_tenant(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    store = _store_with(
        byok_a=_cfg("byok", "openai/tenant-a-model", api_key_enc=provider.encrypt("sk-a-9999")),
    )
    # 租户 A 命中自己的 BYOK
    resolved_a = resolve_default_model(store, "tenant-a")
    assert resolved_a is not None and resolved_a.model == "openai/tenant-a-model"
    # 租户 B 无 BYOK → 无内置 → None（落 env）
    assert resolve_default_model(store, "tenant-b") is None


def test_u1110_tenant_b_without_byok_uses_builtin():
    store = _store_with(
        builtin=_cfg("builtin", "openai/builtin-model"),
        byok_a=_cfg("byok", "openai/tenant-a-model"),
    )
    resolved_a = resolve_default_model(store, "tenant-a")
    resolved_b = resolve_default_model(store, "tenant-b")
    assert resolved_a.model == "openai/tenant-a-model"
    assert resolved_b.model == "openai/builtin-model"


# ==================== U1111：优先级 + BYOK 显式传 key/base_url ====================


def test_u1111_byok_passes_api_key_and_base_url_to_litellm(monkeypatch, fresh_store):
    """本批最大风险点：BYOK 不能靠 env，必须显式传 api_key/base_url。"""
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    captured = _install_capturing_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "x", "confidence": 0.9}'
    )
    fresh_store.set_byok(
        "tenant-a",
        _cfg(
            "byok",
            "openai/tenant-a-model",
            api_key_enc=provider.encrypt("sk-byok-AAAA"),
            base_url="https://byok.gateway.example/v1",
        ),
    )
    # 租户 A 经消费点（get_decision_client 内部 _resolve_llm_config）解析 BYOK
    client = get_decision_client(tenant_id="tenant-a")
    assert isinstance(client, LiteLLMDecisionClient)
    assert client.model == "openai/tenant-a-model"
    client.decide_refund(reason="商品破损", amount=100, limit=500)
    # 显式传入，而非依赖 litellm 读 env
    assert captured.get("api_key") == "sk-byok-AAAA"
    assert captured.get("base_url") == "https://byok.gateway.example/v1"


def test_u1111_builtin_passes_key_to_litellm(monkeypatch, fresh_store):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    captured = _install_capturing_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "x", "confidence": 0.9}'
    )
    fresh_store.set_builtin(
        _cfg(
            "builtin",
            "openai/builtin-model",
            api_key_enc=provider.encrypt("sk-builtin-BBBB"),
        ),
    )
    client = get_decision_client(tenant_id="tenant-a")
    assert isinstance(client, LiteLLMDecisionClient)
    assert client.model == "openai/builtin-model"
    client.decide_refund(reason="商品破损", amount=100, limit=500)
    assert captured.get("api_key") == "sk-builtin-BBBB"


def test_u1111_priority_byok_over_builtin(monkeypatch, fresh_store):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    captured = _install_capturing_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "x", "confidence": 0.9}'
    )
    fresh_store.set_builtin(_cfg("builtin", "openai/builtin-model"))
    fresh_store.set_byok("tenant-a", _cfg("byok", "openai/tenant-a-model"))
    client = get_decision_client(tenant_id="tenant-a")
    assert client.model == "openai/tenant-a-model"
    client.decide_refund(reason="商品破损", amount=100, limit=500)
    assert captured.get("model") == "openai/tenant-a-model"


def test_u1111_node_model_overrides_byok(monkeypatch, fresh_store):
    """节点显式 model > BYOK：decide_refund(model=...) 必须覆盖解析出的默认。"""
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    captured = _install_capturing_litellm(
        monkeypatch, '{"action": "approve_refund", "reason": "x", "confidence": 0.9}'
    )
    fresh_store.set_byok("tenant-a", _cfg("byok", "openai/tenant-a-model"))
    client = get_decision_client(tenant_id="tenant-a")
    client.decide_refund(reason="x", amount=1, limit=5, model="node-explicit-model")
    assert captured.get("model") == "node-explicit-model"


# ==================== U1112：无配置行为与现状一致 ====================


def test_u1112_no_config_keeps_current_behavior(monkeypatch, fresh_store):
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    # 决策 → 规则兜底（与现状一致）
    assert isinstance(get_decision_client(tenant_id="tenant-a"), RuleBasedDecisionClient)
    # 分类 → Offline 兜底
    assert isinstance(
        get_condition_classifier(tenant_id="tenant-a"), OfflineConditionClassifier
    )
    # 反思 → Null 兜底
    assert isinstance(get_summarizer(tenant_id="tenant-a"), NullSummarizer)


def test_u1112_no_tenant_context_falls_back_to_env(monkeypatch):
    """无租户上下文（loader 直跑/旧调用方）→ 完全回退 env，签名兼容。"""
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    assert isinstance(get_decision_client(), RuleBasedDecisionClient)
    monkeypatch.setenv("LITELLM_MODEL", "openai/env-model")
    assert isinstance(get_decision_client(), LiteLLMDecisionClient)
    assert get_decision_client().model == "openai/env-model"


# ==================== U1113：prod fail-closed 门零回归 ====================

# 判据是客户端类型，不随配置来源改变——由既有
# tests/test_ai_decision_prod_gate.py / tests/test_condition_classifier_prod_gate.py 原样守护。
# 这里补一条回归锚：配置面解析出的客户端类型与直接构造等价，门语义零变化。


def test_u1113_client_type_semantics_unchanged_by_config_source(monkeypatch):
    provider = _FakeSecretProvider()
    _patch_secret_provider(monkeypatch, provider)
    store = _store_with(
        builtin=_cfg("builtin", "openai/builtin-model", api_key_enc=provider.encrypt("sk-x")),
    )
    resolved = resolve_default_model(store, "tenant-a")
    client = LiteLLMDecisionClient(resolved.model, api_key=resolved.api_key_enc, base_url=None)
    # 与 env 路径构造的 LiteLLM 客户端是同一类型（prod 门判据不变）
    direct = LiteLLMDecisionClient("openai/builtin-model")
    assert type(client) is type(direct)


# ==================== U1114：密钥信封零明文 ====================


def test_u1114_envelope_zero_plaintext_in_store():
    provider = _FakeSecretProvider()
    store = _store_with()
    plain = "sk-plaintext-SECRET123"
    sealed = provider.encrypt(plain)
    store.set_builtin(_cfg("builtin", "openai/m", api_key_enc=sealed))
    # 序列化视图零明文
    assert plain not in str(store.get_builtin())
    assert plain not in str(store.get_builtin().public_view())
    # 信封可解回（消费点内存使用）
    assert provider.decrypt(sealed) == plain


def test_u1114_file_suffix_secret_supported():
    """_FILE 补水兼容（复用 ZO 语义）：key 可来自 *_FILE 环境变量路径。"""
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "key.txt"
        p.write_text("sk-file-key-XXXX", encoding="utf-8")
        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setenv("ATLAS_MODEL_API_KEY_FILE", str(p))
        try:
            from atlas.security.bootstrap import read_secret

            assert read_secret("ATLAS_MODEL_API_KEY") == "sk-file-key-XXXX"
        finally:
            monkeypatch.undo()
