# -*- coding: utf-8 -*-
"""model-hub 投影同步与解析（W5-3）。

内置档不再由 admin 手工维护单条配置：本模块用 ``model_hub_client`` 拉取 hub
投影（多条模型），把 **api_key 用 atlas 自己的信封（``secrets.py``，enc$v1$...）
加密后**持久化，运行时解析为明文 model/api_key/base_url。

持久化（复用 ``model_config`` 表，无需新 migration）：
- 投影整体存保留行 ``tenant_id='__hub__'``（config 为 {revision, models} JSON）；
- 内存档为进程级单例；PG 档读写 ``model_config``。

解析优先级（与 config.py 一致，内置层换源）：
    显式节点 model > 租户 BYOK > **hub 投影** > env（LITELLM_MODEL）。

凭证纪律：hub 投影含明文 api_key，但只在拉取点内存里用于加密，随后即弃；
落库/投影记录中只有信封。client 的 0600 缓存文件仅作离线兜底。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib import request

logger = logging.getLogger(__name__)

#: hub 投影在 model_config 表中的保留键。
HUB_TENANT_KEY = "__hub__"

DEFAULT_CACHE_PATH = "./data/model-hub-cache.json"
DEFAULT_MODALITY = "text"


@dataclass
class HubModelRecord:
    """投影中的一条模型；api_key 为 atlas 信封，落库零明文。"""

    assignment_id: str
    alias: str | None
    role: str
    model: str  # litellm 模型名（litellm_id 优先，否则原始 model）
    raw_model: str  # hub 原始 model 名
    api_key_enc: str
    base_url: str
    modalities: list[str]
    is_default: bool
    provider: str = ""


@dataclass
class HubProjection:
    """一次 hub 投影（多条模型 + 修订号）。"""

    revision: int
    models: list[HubModelRecord]


@dataclass
class ResolvedHubModel:
    """解析后的明文模型（只活在调用点内存）。"""

    model: str
    api_key: str
    base_url: str
    assignment_id: str
    alias: str | None


# ---------------- 配置 ----------------


def _env(name: str) -> str:
    return os.getenv(name, "").strip()


def is_hub_disabled() -> bool:
    return _env("MODEL_HUB_DISABLED").lower() in ("1", "true", "yes")


def hub_settings() -> tuple[str, str, str] | None:
    """返回 (hub_url, token, cache_path)；未配置或 kill switch 时 None。"""
    if is_hub_disabled():
        return None
    hub_url = _env("MODEL_HUB_URL")
    token = _env("MODEL_HUB_TOKEN")
    if not hub_url or not token:
        return None
    cache_path = _env("MODEL_HUB_CACHE_PATH") or DEFAULT_CACHE_PATH
    return hub_url.rstrip("/"), token, cache_path


def is_hub_configured() -> bool:
    return hub_settings() is not None


def get_hub_client(opener: Any = None) -> Any:
    """按 env 装配 ModelHubClient；未配置返回 None。"""
    settings = hub_settings()
    if settings is None:
        return None
    from model_hub_client import ModelHubClient

    hub_url, token, cache_path = settings
    return ModelHubClient(hub_url, token, cache_path, opener=opener)


# ---------------- 持久化（内存 / PG） ----------------

_projection: HubProjection | None = None
_lock = threading.Lock()


def _projection_to_json(projection: HubProjection) -> dict[str, Any]:
    return asdict(projection)


def _projection_from_json(raw: dict[str, Any]) -> HubProjection:
    return HubProjection(
        revision=int(raw["revision"]),
        models=[HubModelRecord(**m) for m in raw.get("models", [])],
    )


def _save_projection_pg(projection: HubProjection) -> None:
    from sqlalchemy import text

    from atlas.storage.pg import get_pg_backend

    engine = get_pg_backend().engine
    payload = json.dumps(_projection_to_json(projection), ensure_ascii=False)
    from datetime import datetime, timezone

    updated = datetime.now(timezone.utc).isoformat()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO model_config (tenant_id, config, updated_at) "
                "VALUES (:tenant_id, :config, :updated_at) "
                "ON CONFLICT (tenant_id) DO UPDATE SET config = EXCLUDED.config, "
                "updated_at = EXCLUDED.updated_at"
            ),
            {"tenant_id": HUB_TENANT_KEY, "config": payload, "updated_at": updated},
        )


def _load_projection_pg() -> HubProjection | None:
    from sqlalchemy import text

    from atlas.storage.pg import get_pg_backend

    engine = get_pg_backend().engine
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT config FROM model_config WHERE tenant_id = :tenant_id"),
            {"tenant_id": HUB_TENANT_KEY},
        ).first()
    if row is None:
        return None
    return _projection_from_json(json.loads(row[0]))


def save_projection(projection: HubProjection) -> None:
    """保存投影（内存单例 + PG 档；PG 失败不致命）。"""
    global _projection
    with _lock:
        _projection = deepcopy(projection)
    try:
        from atlas.security.bootstrap import read_storage_backend

        if read_storage_backend() == "pg":
            _save_projection_pg(projection)
    except Exception as exc:  # 持久化失败不影响进程内可用
        logger.warning("hub projection PG save failed: %s", exc)


def load_projection() -> HubProjection | None:
    """读取投影（内存单例优先，否则 PG）。"""
    with _lock:
        if _projection is not None:
            return deepcopy(_projection)
    try:
        from atlas.security.bootstrap import read_storage_backend

        if read_storage_backend() == "pg":
            return _load_projection_pg()
    except Exception as exc:
        logger.warning("hub projection PG load failed: %s", exc)
    return None


def reset_hub_projection() -> None:
    """测试辅助：清空内存投影。"""
    global _projection
    with _lock:
        _projection = None


# ---------------- 后台周期刷新 ----------------

REFRESH_INTERVAL_SECONDS = 300.0
_refresh_thread: threading.Thread | None = None
_refresh_stop = threading.Event()
_refresh_thread_lock = threading.Lock()


def _refresh_loop() -> None:
    while not _refresh_stop.wait(REFRESH_INTERVAL_SECONDS):
        try:
            if is_hub_configured():
                sync_hub_projection()
        except Exception as exc:  # noqa: BLE001
            logger.warning("hub projection background refresh failed: %s", exc)


def start_hub_refresh() -> None:
    """启动后台投影刷新线程（5min 一轮；幂等）。"""
    global _refresh_thread
    if not is_hub_configured():
        return
    with _refresh_thread_lock:
        if _refresh_thread is not None and _refresh_thread.is_alive():
            return
        _refresh_stop.clear()
        _refresh_thread = threading.Thread(
            target=_refresh_loop, name="atlas-hub-refresh", daemon=True
        )
        _refresh_thread.start()


def stop_hub_refresh(timeout: float = 2.0) -> None:
    """停止后台刷新线程。"""
    global _refresh_thread
    with _refresh_thread_lock:
        if _refresh_thread is None:
            return
        _refresh_stop.set()
        _refresh_thread.join(timeout=timeout)
        _refresh_thread = None


# ---------------- 同步 ----------------


def _litellm_name(m: dict[str, Any]) -> str:
    litellm_id = m.get("litellm_id")
    if isinstance(litellm_id, str) and litellm_id.strip():
        return litellm_id.strip()
    return str(m.get("model", ""))


_KNOWN_PROVIDERS = {
    "api.openai.com": "openai",
    "api.anthropic.com": "anthropic",
    "api.deepseek.com": "deepseek",
    "dashscope.aliyuncs.com": "qwen",
    "open.bigmodel.cn": "zhipu",
    "api.moonshot.cn": "moonshot",
    "apihub.agnes-ai.com": "agnes",
    "ark.cn-beijing.volces.com": "ark",
}


def provider_from_base_url(base_url: str) -> str:
    """从 base_url 推断 provider 名（与 job-agent providerFromBaseUrl 同口径）。"""
    from urllib.parse import urlparse

    try:
        host = urlparse(base_url).hostname or base_url
    except ValueError:
        host = base_url
    host = host.lower()
    if host in _KNOWN_PROVIDERS:
        return _KNOWN_PROVIDERS[host]
    bare = host.replace("www.", "")
    parts = bare.split(".")
    if len(parts) >= 2:
        return parts[-2]
    return bare.split(":")[0]


def sync_hub_projection(client: Any = None) -> dict[str, int] | None:
    """拉取 hub 投影 → 加密 api_key → 持久化。返回 {revision, pulled}；未配置 None。"""
    hub_client = client or get_hub_client()
    if hub_client is None:
        return None
    from atlas.connections.service import get_secret_provider

    hub_client.pull()
    models = hub_client.list()
    provider = get_secret_provider()

    default_assigned = False
    records: list[HubModelRecord] = []
    for m in models:
        raw_key = str(m.get("api_key", ""))
        api_key_enc = provider.encrypt(raw_key) if raw_key else ""
        is_primary = m.get("role") == "primary"
        is_default = is_primary and not default_assigned
        if is_default:
            default_assigned = True
        records.append(
            HubModelRecord(
                assignment_id=str(m.get("assignment_id", "")),
                alias=m.get("alias"),
                role=str(m.get("role", "primary")),
                model=_litellm_name(m),
                raw_model=str(m.get("model", "")),
                api_key_enc=api_key_enc,
                base_url=str(m.get("base_url", "")),
                modalities=list(m.get("modalities", [])),
                is_default=is_default,
                provider=provider_from_base_url(str(m.get("base_url", ""))),
            )
        )

    revision = hub_client.revision or 0
    projection = HubProjection(revision=int(revision), models=records)
    save_projection(projection)
    return {"revision": int(revision), "pulled": len(records)}


#: atlas 在 hub 中的 project slug。
HUB_PROJECT_SLUG = "atlas"


def report_usage(
    *,
    assignment_id: str,
    model: str,
    provider: str,
    feature: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    billing_source: str = "builtin",
    http_status: int = 200,
    opener: Any = None,
) -> bool:
    """回报一次用量到 hub POST /pub/v1/usage（fire-and-forget；失败只 warning）。

    BYOK（billing_source='byok'）或无 assignment_id 时不回报。
    """
    settings = hub_settings()
    if settings is None or not assignment_id or billing_source == "byok":
        return False
    hub_url, token, _cache_path = settings
    body = {
        "project": HUB_PROJECT_SLUG,
        "feature": feature or HUB_PROJECT_SLUG,
        "billing_source": billing_source,
        "provider": provider,
        "model": model,
        "assignment_id": assignment_id,
        "input_tokens": max(0, int(input_tokens)),
        "output_tokens": max(0, int(output_tokens)),
        "cache_read_tokens": max(0, int(cache_read_tokens)),
        "http_status": int(http_status),
        "cached": False,
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    req = request.Request(
        f"{hub_url}/pub/v1/usage",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        if opener is not None:
            resp = opener(req)
        else:
            resp = request.urlopen(req, timeout=15)
        resp.read()
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("hub usage report failed: %s", exc)
        return False


# ---------------- 解析 ----------------


@dataclass
class RuntimeModel:
    """统一解析后的模型（api_key/base_url 可空，由 litellm 走 env 兜底）。"""

    model: str
    api_key: str | None
    base_url: str | None
    assignment_id: str | None
    source: str  # byok/builtin/hub/env


def resolve_runtime_model(
    tenant_id: str | None,
    *,
    alias: str | None = None,
    modality: str = DEFAULT_MODALITY,
) -> RuntimeModel | None:
    """统一文本模型解析（收口所有 LITELLM_MODEL 直读）：

    1) 显式租户：BYOK > 单条内置（现有 store，迁移期）；
    2) hub 投影（多条，含信封 key）；
    3) env ``LITELLM_MODEL``（只给 model，key/base_url 由 litellm 走 env）。
    都没有返回 None。成功时把计费来源写入 metering contextvar，供用量记录与上报。
    """
    rt: RuntimeModel | None = None
    # 1) 现有 store（BYOK / 单条 builtin）
    if tenant_id is not None:
        from atlas.llm.config import get_model_config_store, resolve_default_model

        cfg = resolve_default_model(get_model_config_store(), tenant_id)
        if cfg is not None:
            from atlas.connections.service import get_secret_provider

            api_key = (
                get_secret_provider().decrypt(cfg.api_key_enc)
                if cfg.api_key_enc
                else None
            )
            rt = RuntimeModel(
                model=cfg.model,
                api_key=api_key,
                base_url=cfg.base_url,
                assignment_id=None,
                source="byok" if cfg.mode == "byok" else "builtin",
            )

    # 2) hub 投影
    if rt is None:
        hub = resolve_hub_model(modality, alias)
        if hub is not None:
            rt = RuntimeModel(
                model=hub.model,
                api_key=hub.api_key,
                base_url=hub.base_url,
                assignment_id=hub.assignment_id,
                source="hub",
            )

    # 3) env 兜底
    if rt is None:
        env_model = os.getenv("LITELLM_MODEL", "").strip()
        if env_model:
            rt = RuntimeModel(
                model=env_model,
                api_key=None,
                base_url=None,
                assignment_id=None,
                source="env",
            )

    if rt is not None:
        from atlas.llm.metering import set_resolved_billing

        billing_source = "byok" if rt.source == "byok" else "builtin"
        set_resolved_billing(rt.assignment_id or "", billing_source)
    return rt


def resolve_hub_model(
    modality: str = DEFAULT_MODALITY,
    alias: str | None = None,
) -> ResolvedHubModel | None:
    """从投影解析一个明文模型。

    - 指定 alias：按 alias 匹配；
    - 否则：该模态下 ``is_default``（首个 primary）优先，再取第一个。
    无投影 / 无匹配 / 无信封 key 时返回 None（调用方回退 env）。
    """
    projection = load_projection()
    if projection is None:
        return None
    from atlas.connections.service import get_secret_provider

    candidates = [
        m for m in projection.models if modality in m.modalities
    ]
    record: HubModelRecord | None = None
    if alias is not None:
        record = next((m for m in candidates if m.alias == alias), None)
    if record is None:
        record = next((m for m in candidates if m.is_default), None)
    if record is None:
        record = candidates[0] if candidates else None
    if record is None or not record.api_key_enc:
        return None
    try:
        api_key = get_secret_provider().decrypt(record.api_key_enc)
    except Exception as exc:
        logger.warning("hub model key decrypt failed: %s", exc)
        return None
    return ResolvedHubModel(
        model=record.model,
        api_key=api_key,
        base_url=record.base_url,
        assignment_id=record.assignment_id,
        alias=record.alias,
    )
