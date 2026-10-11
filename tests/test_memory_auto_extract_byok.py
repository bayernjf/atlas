# -*- coding: utf-8 -*-
"""打包 AN①（docs/122 §6.1 已知限制的闭合）：`memory/auto_extract` 按租户解析模型。

AK 落码时把"租户 BYOK 不生效"记成 harness 级契约变更，实测高估了：执行期注册表本来就
**按租户装配**（``build_runtime_registry``），缺的只是把租户身份带到适配器里。本文件钉三件事：

1. 租户身份真的到了适配器（装配层，不是构造函数默认值）；
2. 到了之后**换的是哪一次 litellm 调用**（BYOK 的 model 与 key 必须出现在出向调用上）；
3. 没配 BYOK 的租户仍落 env 兜底——演示/开发形态不能被这次改动打断。

产出里新增 ``model`` 字段的理由：不带上它，租户配完 BYOK 无从验证提炼到底走了谁的 key，
而这件事软件能报（docs/57「能读出来的事实别写成人看的注释」同族判据）。
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest

from atlas.harness.base import ActionRequest, ActionStatus, Permission
from atlas.harness.runtime import build_runtime_registry
from atlas.llm.config import ModelConfig
from atlas.memory.adapter import MemoryHarnessAdapter
from atlas.memory.items import MemoryStore

_EXTRACT_RESULT = {"facts": [{"content": "客户偏好电话通知", "confidence": 0.95}],
                   "preferences": [], "model": "unresolved"}


class _FakeResponse:
    def __init__(self, content: str):
        self._content = content

    def __getitem__(self, key):
        return {"choices": [{"message": {"content": self._content}}]}[key]


def _fake_litellm(monkeypatch, content: str) -> dict:
    captured: dict = {}

    def _completion(**kwargs):
        captured.update(kwargs)
        return _FakeResponse(content)

    fake = types.ModuleType("litellm")
    fake.completion = _completion
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return captured


class _FakeSecretProvider:
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


@pytest.fixture
def fresh_store(monkeypatch):
    """消费点走 get_model_config_store() 进程单例；重置成干净内存档（照打包 Y 的配方）。"""
    import atlas.llm.config as config_mod

    monkeypatch.setattr(config_mod, "_model_config_store", None)
    return config_mod.get_model_config_store()


@pytest.fixture
def fake_secrets(monkeypatch):
    import atlas.connections.service as conn_service

    provider = _FakeSecretProvider()
    monkeypatch.setattr(conn_service, "get_secret_provider", lambda: provider)
    return provider


def _run(adapter: MemoryHarnessAdapter, text: str = "客户说以后都打电话"):
    return adapter.execute(ActionRequest("auto_extract", {"text": text}))


# --------------------------------------------------------------------------- #
# 1. 装配层：租户身份确实带上了
# --------------------------------------------------------------------------- #


def test_u1357_tenant_services_carry_their_own_tenant_id():
    """身份字段不是装饰：装配出来的这份服务包必须说得出自己属于哪个租户。"""
    from atlas.iam.deps import tenant_registry

    services = tenant_registry.get("t1")
    assert services.tenant_id == "t1"
    assert tenant_registry.get("t2").tenant_id == "t2"
    # 两个租户拿到的不是同一个存储实例（否则 tenant_id 只是装饰，分区才是真的）
    assert services.memory_store is not tenant_registry.get("t2").memory_store


def test_u1358_runtime_registry_clone_carries_the_tenant_and_the_base_one_does_not():
    """执行期克隆带租户；全局发现实例**不该**有租户（它服务所有租户的发现面）。"""
    from atlas.harness.runtime import build_base_registry

    base = build_base_registry(False, None)
    services = SimpleNamespace(
        tenant_id="t9",
        memory_store=MemoryStore(),
        message_service=SimpleNamespace(outbox=[]),
        channel_registry=SimpleNamespace(list=lambda: []),
        openapi_imports=SimpleNamespace(list=lambda: []),
    )
    registry = build_runtime_registry(services, base, None)

    cloned = registry.get("memory")
    assert isinstance(cloned, MemoryHarnessAdapter)
    assert cloned.tenant_id == "t9", "克隆实例必须知道自己是哪个租户的"
    assert cloned.repo is services.memory_store

    assert base.get("memory").tenant_id is None, "全局发现实例不持有租户身份"


# --------------------------------------------------------------------------- #
# 2. 透传：租户身份进到了模型解析
# --------------------------------------------------------------------------- #


def test_u1359_auto_extract_passes_its_tenant_to_the_resolver(monkeypatch):
    seen: dict = {}

    def _capture(**kwargs):
        seen.update(kwargs)
        return dict(_EXTRACT_RESULT)

    monkeypatch.setattr("atlas.memory.adapter.extract_memories", _capture)
    adapter = MemoryHarnessAdapter(repo=MemoryStore(), granted_permissions={Permission.WRITE},
                                   tenant_id="t1")
    result = _run(adapter)
    assert result.status == ActionStatus.SUCCESS
    assert seen["tenant_id"] == "t1"


def test_u1360_byok_model_and_key_are_the_ones_called(monkeypatch, fresh_store, fake_secrets):
    """真语义：租户配了 BYOK，出向 litellm 调用就得是那个 model＋那条 key。"""
    key = "sk-tenant-a-secret"
    fresh_store.set_byok(
        "t1",
        ModelConfig(mode="byok", model="openai/tenant-a-model",
                    api_key_enc=fake_secrets.encrypt(key), base_url="https://a.example/v1"),
    )
    # 内置也在，且与 BYOK 不同——用来证明优先级真的是 BYOK > 内置，而不是碰巧
    fresh_store.set_builtin(ModelConfig(mode="builtin", model="openai/builtin-model"))
    captured = _fake_litellm(monkeypatch, '{"facts": [], "preferences": []}')

    adapter = MemoryHarnessAdapter(repo=MemoryStore(), granted_permissions={Permission.WRITE},
                                   tenant_id="t1")
    out = _run(adapter).output

    assert captured["model"] == "openai/tenant-a-model"
    assert captured["api_key"] == key
    assert captured["base_url"] == "https://a.example/v1"
    assert out["model"] == "openai/tenant-a-model", "产出要报出实际用了哪个模型"


# --------------------------------------------------------------------------- #
# 3. 兜底：没配 BYOK 的租户与全局实例都不能被打断
# --------------------------------------------------------------------------- #


def test_u1361_tenant_without_byok_falls_back_to_env(monkeypatch, fresh_store):
    """干净 store（无 BYOK 无内置）⇒ 落 env，演示/开发形态逐字不变。"""
    monkeypatch.setenv("LITELLM_MODEL", "openai/env-model")
    captured = _fake_litellm(monkeypatch, '{"facts": [], "preferences": []}')

    adapter = MemoryHarnessAdapter(repo=MemoryStore(), granted_permissions={Permission.WRITE},
                                   tenant_id="t-没有配置")
    result = _run(adapter)

    assert result.status == ActionStatus.SUCCESS
    assert captured["model"] == "openai/env-model"
    assert "api_key" not in captured, "env 兜底不显式传 key，由 litellm 自读"
    assert result.output["model"] == "openai/env-model"


def test_u1362_global_discovery_adapter_still_resolves_via_env(monkeypatch, fresh_store):
    """tenant_id=None（全局实例／无租户上下文）⇒ 解析层按 None 走，不抛、不猜租户。"""
    monkeypatch.setenv("LITELLM_MODEL", "openai/env-model")
    _fake_litellm(monkeypatch, '{"facts": [], "preferences": []}')

    adapter = MemoryHarnessAdapter(repo=MemoryStore(), granted_permissions={Permission.WRITE})
    assert adapter.tenant_id is None
    result = _run(adapter)
    assert result.status == ActionStatus.SUCCESS
    assert result.output["model"] == "openai/env-model"


def test_u1363_no_model_at_all_is_reported_not_disguised(monkeypatch, fresh_store):
    """env 也空 ⇒ 明说 llm_unavailable，且 model 是空串而不是假模型名。"""
    monkeypatch.delenv("LITELLM_MODEL", raising=False)
    _fake_litellm(monkeypatch, "{}")

    adapter = MemoryHarnessAdapter(repo=MemoryStore(), granted_permissions={Permission.WRITE},
                                   tenant_id="t1")
    out = _run(adapter).output
    assert out["skipped"] == "llm_unavailable"
    assert out["extracted"] == 0
    assert out["model"] == ""
