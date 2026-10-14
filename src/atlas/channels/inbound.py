# -*- coding: utf-8 -*-
"""入站消息接收：去重 → 会话↔图映射 → 起 run → 回写会话（打包 BO，docs/131）。

复用 channels/webhooks.py（WebhookDeliverer）的范式——后台线程起 run、失败落 failed
不回传触发方——但维度从 shopify 专属换为通用 channel（D-D1/D-D2/D-D3）。真供应商
verify 留 ⛔（docs/73 1.3），本批只用假供应商（可插拔 verify 钩子在端点层，passthrough）。
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Protocol

from pydantic import BaseModel

from atlas.channels.base import ChannelBinding
from atlas.channels.inbound_deliveries import InMemoryInboundStore
from atlas.routing.models import TriggerEvent

logger = logging.getLogger(__name__)

Resolver = Callable[[str, str, TriggerEvent], int | None]
# (graph_id, version, event, tenant, agent_id) —— 复用 _webhook_trigger 同形（agent_id 缺省 ''）。
TriggerFn = Callable[[str, int, TriggerEvent, str, str], None]


class InboundEnvelope(BaseModel):
    """入站消息信封（假供应商格式；真供应商由端点层验签后构造）。"""

    message_id: str
    conversation_id: str
    channel: str
    data: dict[str, Any] = {}


class InMemoryInboundSessionStore:
    """进程内会话记录（假供应商回写目标；docs/131 D-D7）。reset 由租户注册表统一清。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, list[dict[str, Any]]] = {}

    def append(self, tenant_id: str, message: dict[str, Any]) -> None:
        conv = message.get("conversation_id", "")
        with self._lock:
            self._sessions.setdefault(conv, []).append(dict(message))

    def list(self, tenant_id: str, conversation_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(m) for m in self._sessions.get(conversation_id, [])]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InboundReceiver:
    """验签后的投递：去重 → 映射命中 → resolve 钉版本 → 后台触发；异常仅 warning。

    与 WebhookDeliverer 同构，但映射（D-P1）按 conversation_id 命中（挂 binding），
    且触发时带入 agent_id（D74）。未命中映射 / 无发布版本 / 触发失败 ⇒ 死信（不静默吞）。
    """

    def __init__(
        self,
        *,
        resolver: Resolver | None = None,
        trigger: TriggerFn | None = None,
        store_provider: Callable[[str], Any] | None = None,
        session_store: InMemoryInboundSessionStore | None = None,
        auditor: Callable[[str, str, dict], None] | None = None,
    ) -> None:
        self._resolve = resolver
        self._trigger = trigger
        self._store_provider = store_provider
        self._session_store = session_store
        self._audit = auditor

    def receive(
        self, tenant_id: str, binding_id: str, binding: ChannelBinding, envelope: InboundEnvelope
    ) -> dict[str, Any]:
        store = self._store_for(tenant_id)
        # D-D2 去重（幂等，重复消息不二次起 run）
        if store is not None:
            try:
                if store.note_duplicate_if_seen(tenant_id, envelope.message_id):
                    return {"duplicate": True}
            except Exception:
                logger.warning("inbound 投递存储不可用，退回内存档", exc_info=True)
                store = None

        if self._audit is not None:
            try:
                self._audit(
                    "channel.inbound_received",
                    tenant_id,
                    {
                        "bindingId": binding_id,
                        "messageId": envelope.message_id,
                        "conversationId": envelope.conversation_id,
                    },
                )
            except Exception:  # pragma: no cover - 审计失败不影响主流程
                logger.warning("inbound 审计写入失败", exc_info=True)

        # D-P1 会话↔图映射命中（挂 binding，不做独立实体）
        mapping = self._active_mapping(binding, envelope.conversation_id)
        if mapping is None or not mapping.get("graph_id"):
            if store is not None:
                self._record_dead(
                    store, tenant_id, binding_id, envelope,
                    reasons=[{"code": "NO_MAPPING" if mapping is None else "MISSING_GRAPH_ID"}],
                )
            return {"deadLetter": True, "messageId": envelope.message_id}

        graph_id = str(mapping["graph_id"])
        agent_id = mapping.get("agent_id") or ""
        event = TriggerEvent(
            channel=envelope.channel,
            payload={
                "conversationId": envelope.conversation_id,
                "messageId": envelope.message_id,
                "data": envelope.data,
            },
        )
        version = self._resolve(graph_id, tenant_id, event) if self._resolve is not None else None
        if version is None:
            if store is not None:
                self._record_dead(
                    store, tenant_id, binding_id, envelope,
                    reasons=[{"code": "NO_PUBLISHED_VERSION"}],
                )
            return {"deadLetter": True, "messageId": envelope.message_id}

        run_id = uuid.uuid4().hex
        try:
            if self._trigger is not None:
                self._trigger(graph_id, version, event, tenant_id, agent_id)
        except Exception:
            logger.warning("inbound 触发图运行失败 graph=%s", graph_id, exc_info=True)
            if store is not None:
                self._record_dead(
                    store, tenant_id, binding_id, envelope,
                    reasons=[{"code": "TRIGGER_FAILED"}],
                )
            return {"deadLetter": True, "messageId": envelope.message_id}

        if store is not None:
            self._record_received(store, tenant_id, binding_id, envelope)
        # D-D7 回写会话（假供应商落进程内记录；run outputs 由会话视图聚合 runs 取）
        if self._session_store is not None:
            self._session_store.append(
                tenant_id,
                {
                    "role": "user",
                    "conversation_id": envelope.conversation_id,
                    "messageId": envelope.message_id,
                    "runId": run_id,
                    "data": envelope.data,
                    "createdAt": _now_iso(),
                },
            )
        return {"received": True, "runId": run_id, "agentId": agent_id}

    def replay(
        self, tenant_id: str, binding_id: str, binding: ChannelBinding, message_id: str
    ) -> dict[str, Any] | None:
        """死信重放：绕过去重，按当前映射重投（仿 webhook 的 replay）。"""
        store = self._store_for(tenant_id)
        if store is None:
            return None
        dead = store.get_dead(tenant_id, message_id)
        if dead is None:
            return None
        envelope = InboundEnvelope(
            message_id=message_id,
            conversation_id=dead.get("conversationId", ""),
            channel=dead.get("channel", ""),
            data=dead.get("data") or {},
        )
        result = self.receive(tenant_id, binding_id, binding, envelope)
        received = result.get("received", False)
        try:
            store.resolve_replay(
                tenant_id, message_id,
                received=received,
                reasons=None if received else [{"code": "REPLAY_FAILED"}],
            )
        except Exception:
            logger.warning("inbound 重放状态写回失败", exc_info=True)
        return {
            "messageId": message_id,
            "status": "received" if received else "dead",
            **result,
        }

    # --- 内部辅助 ---------------------------------------------------------

    def _active_mapping(self, binding: ChannelBinding, conversation_id: str) -> dict[str, Any] | None:
        for m in binding.inbound_mappings:
            if m.get("conversation_id") == conversation_id and m.get("enabled", True):
                return m
        return None

    def _record_received(
        self, store: Any, tenant_id: str, binding_id: str, envelope: InboundEnvelope
    ) -> None:
        try:
            store.record_received(
                tenant_id,
                message_id=envelope.message_id,
                binding_id=binding_id,
                channel=envelope.channel,
                conversation_id=envelope.conversation_id,
            )
        except Exception:  # pragma: no cover - 写库失败不影响主流程
            logger.warning("inbound 投递结果写库失败", exc_info=True)

    def _record_dead(
        self, store: Any, tenant_id: str, binding_id: str,
        envelope: InboundEnvelope, reasons: list[dict[str, str]],
    ) -> None:
        try:
            store.record_dead(
                tenant_id,
                message_id=envelope.message_id,
                binding_id=binding_id,
                channel=envelope.channel,
                conversation_id=envelope.conversation_id,
                reasons=reasons,
                payload=envelope.data,
            )
        except Exception:  # pragma: no cover - 写库失败不影响主流程
            logger.warning("inbound 死信写库失败", exc_info=True)

    def _store_for(self, tenant_id: str) -> Any:
        if self._store_provider is None:
            return None
        try:
            return self._store_provider(tenant_id)
        except Exception:
            logger.warning("inbound 投递存储解析失败", exc_info=True)
            return None
