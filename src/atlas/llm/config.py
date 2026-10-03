# -*- coding: utf-8 -*-
"""LLM 模型配置管理面（docs/93 打包 Y）：内置模型（admin 管理）＋BYOK（用户自配置）。

数据模型与两档 store（内存/PG）：
- ``ModelConfig``：mode/model/api_key_enc/base_url/enabled/updated_by/updated_at。
  api_key_enc 一律为 AES-GCM 信封（docs/32 T26，enc$v1$...），落库零明文。
- ``ModelConfigStore``：内置（全局一份，tenant_id 保留键 ``__builtin__``）＋BYOK（per-tenant 一份）。
  内存档进程内 dict；PG 档照 monitoring_rules 单行 JSON 先例（004/038）落 model_config 表。
- 进程级单例 ``get_model_config_store()``：照 connections.service.get_secret_provider() 模式，
  按 ``ATLAS_STORAGE_BACKEND`` 分档；挂 ``TenantServices.model_config``（reset 不清，PG 档持久化）。

消费点（llm/decision.py、llm/condition_classifier.py、llm/nl_generate.py、
reflection/adapter.py）经本模块解析「构造期默认模型」：
优先级＝显式传参（节点 model，既有）> 租户 BYOK > 内置 > env（LITELLM_MODEL，Demo/dev 兜底）。
无租户上下文（loader 直跑/测试）时回退 env，签名与行为现状完全一致。
"""

from __future__ import annotations

import logging
import threading
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Literal, Protocol

from atlas.security.bootstrap import read_storage_backend

logger = logging.getLogger(__name__)

#: 内置模型在 store 中的保留键（平台级单一配置，admin 维护）。
BUILTIN_TENANT_KEY = "__builtin__"

ModelMode = Literal["builtin", "byok"]


@dataclass
class ModelConfig:
    """一条 LLM 模型配置；api_key_enc 为 AES-GCM 信封，明文只活在调用点内存。"""

    mode: ModelMode
    model: str  # litellm 模型名，如 openai/agnes-2.5-flash
    api_key_enc: str = ""  # AES-GCM 信封（enc$v1$...）；空＝无 key
    base_url: str | None = None  # litellm base_url（如 openai 兼容网关）
    enabled: bool = True
    updated_by: str = ""
    updated_at: str = ""

    def public_view(self) -> dict:
        """HTTP 返回视图：api_key 只回 *** 后 4 位（脱敏，零明文泄漏）。"""
        tail = self.api_key_enc[-4:] if self.api_key_enc else ""
        return {
            "mode": self.mode,
            "model": self.model,
            "apiKey": f"***{tail}" if tail else "",
            "baseUrl": self.base_url,
            "enabled": self.enabled,
            "updatedBy": self.updated_by,
            "updatedAt": self.updated_at,
        }


class ModelConfigStore(Protocol):
    """两档 store 的公共接缝（内存/PG 实现互换）。"""

    def get_builtin(self) -> ModelConfig | None: ...

    def set_builtin(self, cfg: ModelConfig) -> ModelConfig: ...

    def get_byok(self, tenant_id: str) -> ModelConfig | None: ...

    def set_byok(self, tenant_id: str, cfg: ModelConfig) -> ModelConfig: ...


class InMemoryModelConfigStore:
    """进程内 dict 档；内置键 ``__builtin__``，BYOK 按真实 tenant_id 分行。"""

    def __init__(self) -> None:
        self._data: dict[str, ModelConfig] = {}
        self._lock = threading.Lock()

    def _get(self, key: str) -> ModelConfig | None:
        with self._lock:
            raw = self._data.get(key)
            return deepcopy(raw) if raw is not None else None

    def _set(self, key: str, cfg: ModelConfig) -> ModelConfig:
        with self._lock:
            self._data[key] = deepcopy(cfg)
            return deepcopy(cfg)

    def get_builtin(self) -> ModelConfig | None:
        return self._get(BUILTIN_TENANT_KEY)

    def set_builtin(self, cfg: ModelConfig) -> ModelConfig:
        return self._set(BUILTIN_TENANT_KEY, cfg)

    def get_byok(self, tenant_id: str) -> ModelConfig | None:
        return self._get(tenant_id)

    def set_byok(self, tenant_id: str, cfg: ModelConfig) -> ModelConfig:
        return self._set(tenant_id, cfg)


class PgModelConfigStore:
    """PG 档：照 monitoring_rules 单行 JSON 先例，model_config 表（迁移 040）。

    内置保留行 tenant_id='__builtin__'；BYOK 一行一租户。ON CONFLICT 幂等 upsert。
    """

    def __init__(self, engine: object) -> None:
        self._engine = engine

    def _get_row(self, tenant_id: str) -> ModelConfig | None:
        import json

        from sqlalchemy import text

        with self._engine.connect() as conn:
            row = conn.execute(
                text("SELECT config FROM model_config WHERE tenant_id = :tenant_id"),
                {"tenant_id": tenant_id},
            ).first()
        if row is None:
            return None
        raw = json.loads(row[0])
        return ModelConfig(**raw)

    def _upsert(self, tenant_id: str, cfg: ModelConfig) -> ModelConfig:
        import json

        from sqlalchemy import text

        with self._engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO model_config (tenant_id, config, updated_at) "
                    "VALUES (:tenant_id, :config, :updated_at) "
                    "ON CONFLICT (tenant_id) DO UPDATE SET config = EXCLUDED.config, "
                    "updated_at = EXCLUDED.updated_at"
                ),
                {
                    "tenant_id": tenant_id,
                    "config": json.dumps(asdict(cfg), ensure_ascii=False),
                    "updated_at": cfg.updated_at,
                },
            )
        return deepcopy(cfg)

    def get_builtin(self) -> ModelConfig | None:
        return self._get_row(BUILTIN_TENANT_KEY)

    def set_builtin(self, cfg: ModelConfig) -> ModelConfig:
        return self._upsert(BUILTIN_TENANT_KEY, cfg)

    def get_byok(self, tenant_id: str) -> ModelConfig | None:
        return self._get_row(tenant_id)

    def set_byok(self, tenant_id: str, cfg: ModelConfig) -> ModelConfig:
        return self._upsert(tenant_id, cfg)


# ---------------- 进程级单例（照 connections.service.get_secret_provider 模式） ----------------

_model_config_store: ModelConfigStore | None = None
_store_lock = threading.Lock()


def get_model_config_store() -> ModelConfigStore:
    """按 STORAGE_BACKEND 装配进程级单例 store（内置全局一份＋BYOK per-tenant）。

    PG 档复用 get_pg_backend().engine（迁移 040 表）；内存档进程内 dict。
    reset 不清（照 connection_service 语义：模型配置跨租户重置保留）。
    """
    global _model_config_store
    if _model_config_store is None:
        with _store_lock:
            if _model_config_store is None:
                if read_storage_backend() == "pg":
                    from atlas.storage.pg import get_pg_backend

                    _model_config_store = PgModelConfigStore(get_pg_backend().engine)
                else:
                    _model_config_store = InMemoryModelConfigStore()
    return _model_config_store


def resolve_default_model(
    store: ModelConfigStore, tenant_id: str | None
) -> ModelConfig | None:
    """按优先级解析「构造期默认模型」：租户 BYOK > 内置；无租户上下文（None）→ None（回退 env）。

    契约 docs/93 §1 语义约束 3：未配置节点 model 时，租户配了 BYOK 用 BYOK，否则用内置；
    env（LITELLM_MODEL）降为最后兜底（本函数返回 None 时由消费点落 env）。
    节点级 model 覆盖（显式传参）在消费点调用处优先，不进本函数。
    """
    if tenant_id is None:
        return None
    byok = store.get_byok(tenant_id)
    if byok is not None and byok.enabled and byok.model.strip():
        return byok
    builtin = store.get_builtin()
    if builtin is not None and builtin.enabled and builtin.model.strip():
        return builtin
    return None
