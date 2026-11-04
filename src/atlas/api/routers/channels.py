"""P2-1 split: Channel bindings, webhooks, inbound messages and remote hooks.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
import asyncio
import json
from atlas.channels.base import CHANNEL_AUTHORIZATION_ERROR_CODES, CHANNEL_UPSTREAM_UNAUTHORIZED, ChannelError
from atlas.channels.inbound import InboundEnvelope
from atlas.channels.webhooks import HMAC_HEADER, SUPPORTED_TOPICS, build_envelope, verify_shopify_hmac
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from fastapi import Depends, HTTPException, Request
from typing import Any
from atlas.api.main import (
    ChannelBindRequest,
    InboundMappingsRequest,
    MAX_WEBHOOK_BODY_BYTES,
    MAX_WEBHOOK_SUBSCRIPTIONS,
    RemoteWebhookRequest,
    WebhookSubscriptionsRequest,
    _channel_http_error,
    _inbound_audit,
    _inbound_failure_limiter,
    _inbound_receiver,
    _locate_binding,
    _record_channel_audit,
    _webhook_deliverer,
)


router: APIRouter = APIRouter()


@router.get("/api/channels")
def list_channels(principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    return {"items": _main.services_for(principal).channel_registry.list()}


@router.post("/api/channels", status_code=201)
def bind_channel(
    body: ChannelBindRequest,
    http_request: Request,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    services = _main.services_for(principal)
    try:
        view = services.channel_registry.bind(
            body.provider, body.connectionId, body.config,
            created_by=principal.username,
        )
    except ChannelError as exc:
        _record_channel_audit(services, principal, http_request, "channel.bind", exc.status_code)
        raise _channel_http_error(exc)
    _record_channel_audit(services, principal, http_request, "channel.bind", 201)
    return view


@router.get("/api/channels/{binding_id}")
def get_channel(binding_id: str, principal: Principal = Depends(require("read"))) -> dict[str, Any]:
    try:
        return _main.services_for(principal).channel_registry.get(binding_id)
    except ChannelError as exc:
        raise _channel_http_error(exc)


@router.post("/api/channels/{binding_id}/test")
def test_channel(
    binding_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    """连通性测试：真出向＋写 binding.status，故与兄弟端点 `/api/connections/{id}/test` 同档（operate）。

    docs/89 §15 A-3：此前挂在 `read` 档——一个只读角色既能让平台对外发起请求，又能把绑定
    状态改成 connected/error，是与 RBAC 白名单相违的写面。
    """
    # 上游错误不 5xx：200 体 ok=false（docs/38 §1C）
    return _main.services_for(principal).channel_registry.test(binding_id)


@router.delete("/api/channels/{binding_id}")
def delete_channel(
    binding_id: str,
    http_request: Request,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    services = _main.services_for(principal)
    try:
        deleted = services.channel_registry.delete(binding_id)
    except ChannelError as exc:
        _record_channel_audit(services, principal, http_request, "channel.unbind", exc.status_code)
        raise _channel_http_error(exc)
    _record_channel_audit(services, principal, http_request, "channel.unbind", 200)
    return {"deleted": deleted}


@router.post("/api/channels/hooks/shopify/{binding_id}")
async def shopify_webhook_ingress(binding_id: str, http_request: Request) -> dict[str, Any]:
    """Shopify 公开入站：先跨租户定位绑定，再在原始 body 上 HMAC 验签，最后才解析 JSON。

    验签通过后一律 200（received/duplicate/ignored）；绑定不存在统一 404、
    签名失败统一 401（不泄漏原因差异）；密钥不可用 503；body/头非法 400。
    """
    located = await asyncio.to_thread(_locate_binding, binding_id)
    if located is None:
        raise HTTPException(status_code=404, detail="渠道绑定不存在")
    tenant_id, binding, services = located
    secret = services.connection_service.client_secret_for(binding.connection_id)
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook 验签密钥暂不可用")
    raw = await http_request.body()
    if len(raw) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="Webhook 请求体超过 1MB 上限")
    signature = http_request.headers.get(HMAC_HEADER)
    if not verify_shopify_hmac(raw, signature, secret):
        raise HTTPException(status_code=401, detail="Webhook 签名校验失败")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="Webhook 请求体不是合法 JSON 对象")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Webhook 请求体必须是 JSON 对象")
    try:
        envelope = build_envelope(http_request.headers, data)
    except ChannelError as exc:
        raise _channel_http_error(exc)
    return _webhook_deliverer.deliver(
        tenant_id,
        {"id": binding.id, "webhook_subscriptions": binding.webhook_subscriptions},
        envelope,
    )


@router.get("/api/channels/{binding_id}/webhooks")
def get_webhook_subscriptions(
    binding_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    try:
        items = _main.services_for(principal).channel_registry.subscriptions(binding_id)
    except ChannelError as exc:
        raise _channel_http_error(exc)
    return {"items": items}


@router.put("/api/channels/{binding_id}/webhooks")
def put_webhook_subscriptions(
    binding_id: str,
    body: WebhookSubscriptionsRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    services = _main.services_for(principal)
    try:
        services.channel_registry._require(binding_id)
    except ChannelError as exc:
        raise _channel_http_error(exc)

    subs = body.subscriptions
    errors: list[str] = []
    if not isinstance(subs, list):
        errors.append("订阅必须是数组")
    elif len(subs) > MAX_WEBHOOK_SUBSCRIPTIONS:
        errors.append(f"订阅数量不能超过 {MAX_WEBHOOK_SUBSCRIPTIONS} 条")
    normalized: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(subs if isinstance(subs, list) else []):
        if not isinstance(item, dict):
            errors.append(f"第 {index + 1} 条订阅格式非法")
            continue
        topic = item.get("topic")
        graph_id = item.get("graphId")
        enabled = item.get("enabled", True)
        prefix = f"第 {index + 1} 条订阅"
        if topic not in SUPPORTED_TOPICS:
            errors.append(f"{prefix}：不支持的 Webhook topic：{topic}")
        if not isinstance(graph_id, str) or not graph_id.strip():
            errors.append(f"{prefix}：缺少 graphId")
        elif services.graph_store.get(graph_id.strip()) is None:
            errors.append(f"{prefix}：订阅的图不存在：{graph_id}")
        if not isinstance(enabled, bool):
            errors.append(f"{prefix}：enabled 必须是布尔值")
        if isinstance(topic, str) and isinstance(graph_id, str):
            key = (topic, graph_id.strip())
            if key in seen:
                errors.append(f"{prefix}：同一 topic 与图的订阅重复：{topic} → {graph_id}")
            seen.add(key)
        if isinstance(graph_id, str):
            graph_id = graph_id.strip()
        normalized.append({"topic": topic, "graph_id": graph_id, "enabled": enabled})
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    items = services.channel_registry.set_subscriptions(binding_id, normalized)
    return {"items": items}


@router.get("/api/channels/{binding_id}/inbound-mappings")
def get_inbound_mappings(
    binding_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    try:
        items = _main.services_for(principal).channel_registry.inbound_mappings(binding_id)
    except ChannelError as exc:
        raise _channel_http_error(exc)
    return {"items": items}


@router.put("/api/channels/{binding_id}/inbound-mappings")
def put_inbound_mappings(
    binding_id: str,
    body: InboundMappingsRequest,
    principal: Principal = Depends(require("administer")),
) -> dict[str, Any]:
    services = _main.services_for(principal)
    try:
        services.channel_registry._require(binding_id)
    except ChannelError as exc:
        raise _channel_http_error(exc)

    mappings = body.mappings
    errors: list[str] = []
    if not isinstance(mappings, list):
        errors.append("映射必须是数组")
    elif len(mappings) > MAX_WEBHOOK_SUBSCRIPTIONS:
        errors.append(f"映射数量不能超过 {MAX_WEBHOOK_SUBSCRIPTIONS} 条")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(mappings if isinstance(mappings, list) else []):
        prefix = f"第 {index + 1} 条映射"
        if not isinstance(item, dict):
            errors.append(f"{prefix}格式非法")
            continue
        conversation_id = item.get("conversationId")
        graph_id = item.get("graphId")
        agent_id = item.get("agentId")
        enabled = item.get("enabled", True)
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            errors.append(f"{prefix}：缺少 conversationId")
        if not isinstance(graph_id, str) or not graph_id.strip():
            errors.append(f"{prefix}：缺少 graphId")
        elif services.graph_store.get(graph_id.strip()) is None:
            errors.append(f"{prefix}：映射的图不存在：{graph_id}")
        if agent_id is not None:
            if not isinstance(agent_id, str) or not agent_id.strip():
                errors.append(f"{prefix}：agentId 必须是非空字符串")
            elif services.agents_store.get(principal.tenant_id, agent_id.strip()) is None:
                errors.append(f"{prefix}：映射的 agent 不存在：{agent_id}")
        if not isinstance(enabled, bool):
            errors.append(f"{prefix}：enabled 必须是布尔值")
        if isinstance(conversation_id, str) and conversation_id.strip():
            key = conversation_id.strip()
            if key in seen:
                errors.append(f"{prefix}：同一会话的映射重复：{key}")
            seen.add(key)
        normalized.append({
            "conversation_id": conversation_id.strip() if isinstance(conversation_id, str) else conversation_id,
            "graph_id": graph_id.strip() if isinstance(graph_id, str) else graph_id,
            "agent_id": agent_id.strip() if isinstance(agent_id, str) else None,
            "enabled": enabled,
        })
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    items = services.channel_registry.set_inbound_mappings(binding_id, normalized)
    return {"items": items}


@router.get("/api/channels/webhooks/dead-letters")
def list_webhook_dead_letters(
    topic: str | None = None,
    bindingId: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    store = _main.services_for(principal).webhook_deliveries
    items = store.list_dead(
        principal.tenant_id, topic=topic, binding_id=bindingId, limit=limit
    )
    return {"items": items}


@router.post("/api/channels/webhooks/dead-letters/{webhook_id}/replay")
def replay_webhook_dead_letter(
    webhook_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    services = _main.services_for(principal)
    store = services.webhook_deliveries
    dead = store.get_dead(principal.tenant_id, webhook_id)
    if dead is None:
        raise HTTPException(status_code=404, detail="死信投递不存在或已处理")
    binding = services.channel_registry._store.get(dead["bindingId"])
    if binding is None:
        raise HTTPException(status_code=404, detail="死信所属的渠道绑定不存在")
    result = _webhook_deliverer.replay(
        principal.tenant_id,
        {"id": binding.id, "webhook_subscriptions": binding.webhook_subscriptions},
        webhook_id,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="死信投递不存在或已处理")
    return result


@router.delete("/api/channels/webhooks/dead-letters/{webhook_id}")
def delete_webhook_dead_letter(
    webhook_id: str, principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    store = _main.services_for(principal).webhook_deliveries
    deleted = store.delete(principal.tenant_id, webhook_id)
    return {"deleted": deleted}


@router.get("/api/channels/webhooks/metrics")
def webhook_delivery_metrics(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    store = _main.services_for(principal).webhook_deliveries
    return store.metrics(principal.tenant_id)


@router.post("/api/channels/inbound/{binding_id}")
async def inbound_ingress(binding_id: str, http_request: Request) -> dict[str, Any]:
    """通用入站接收（公开，强制验签；D-D1 + P1 fail-closed 修复）。

    验签：binding.config.inbound_secret 必须存在，否则 403；存在则 HMAC-SHA256
    验签（header X-Inbound-Signature，恒定时间比对），失败 401 并限流 429。
    重放由接收侧 messageId 幂等去重拦截（{duplicate}）。
    响应：{received, runId?, agentId?} / {duplicate} / {deadLetter, messageId?}。
    """
    located = await asyncio.to_thread(_locate_binding, binding_id)
    if located is None:
        raise HTTPException(status_code=404, detail="渠道绑定不存在")
    tenant_id, binding, services = located
    raw = await http_request.body()
    if len(raw) > MAX_WEBHOOK_BODY_BYTES:
        raise HTTPException(status_code=413, detail="入站请求体超过 1MB 上限")
    secret = (binding.config or {}).get("inbound_secret")
    if not secret:
        # fail-closed（P1 修复）：原"未配 secret 即 passthrough"分支是未认证远程触发面
        # ——binding id 可枚举（ch-{seq}），命中即可以可控 payload 触发该租户已发布图。
        # 与同仓 Shopify webhook「密钥缺失 503」、A2A prod 无 token 关闭同纪律。
        _inbound_audit("channel.inbound_secret_missing", tenant_id, {"bindingId": binding_id})
        raise HTTPException(status_code=403, detail="渠道绑定未配置入站验签密钥，拒绝受理")
    client_key = http_request.client.host if http_request.client else "unknown"
    if not _inbound_failure_limiter.allow(client_key):
        _inbound_audit("channel.inbound_rate_limited", tenant_id, {"bindingId": binding_id})
        raise HTTPException(status_code=429, detail="验签失败过于频繁，请稍后再试")
    signature = http_request.headers.get("X-Inbound-Signature")
    if not verify_shopify_hmac(raw, signature, secret):
        _inbound_failure_limiter.note_failure(client_key)
        _inbound_audit("channel.inbound_signature_failed", tenant_id, {"bindingId": binding_id})
        raise HTTPException(status_code=401, detail="入站签名校验失败")
    _inbound_failure_limiter.note_success(client_key)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="入站请求体不是合法 JSON 对象")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="入站请求体必须是 JSON 对象")
    message_id = data.get("messageId") or data.get("message_id")
    conversation_id = data.get("conversationId") or data.get("conversation_id")
    channel = data.get("channel") or "fake"
    payload = data.get("data", data)
    if not message_id or not conversation_id:
        raise HTTPException(status_code=400, detail="入站消息缺少 messageId / conversationId")
    envelope = InboundEnvelope(
        message_id=message_id, conversation_id=conversation_id, channel=channel, data=payload
    )
    return _inbound_receiver.receive(tenant_id, binding_id, binding, envelope)


@router.get("/api/channels/inbound/dead-letters")
def list_inbound_dead_letters(
    channel: str | None = None,
    bindingId: str | None = None,
    limit: int = 100,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    store = _main.services_for(principal).inbound_deliveries
    items = store.list_dead(
        principal.tenant_id, channel=channel, binding_id=bindingId, limit=limit
    )
    return {"items": items}


@router.post("/api/channels/inbound/dead-letters/{message_id}/replay")
def replay_inbound_dead_letter(
    message_id: str, principal: Principal = Depends(require("operate"))
) -> dict[str, Any]:
    services = _main.services_for(principal)
    dead = services.inbound_deliveries.get_dead(principal.tenant_id, message_id)
    if dead is None:
        raise HTTPException(status_code=404, detail="死信投递不存在或已处理")
    binding_obj = services.channel_registry._store.get(dead["bindingId"])
    if binding_obj is None:
        raise HTTPException(status_code=404, detail="死信所属的渠道绑定不存在")
    result = _inbound_receiver.replay(
        principal.tenant_id, dead["bindingId"], binding_obj, message_id
    )
    if result is None:
        raise HTTPException(status_code=404, detail="死信投递不存在或已处理")
    return result


@router.delete("/api/channels/inbound/dead-letters/{message_id}")
def delete_inbound_dead_letter(
    message_id: str, principal: Principal = Depends(require("administer"))
) -> dict[str, Any]:
    deleted = _main.services_for(principal).inbound_deliveries.delete(principal.tenant_id, message_id)
    return {"deleted": deleted}


@router.get("/api/channels/inbound/metrics")
def inbound_delivery_metrics(
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    return _main.services_for(principal).inbound_deliveries.metrics(principal.tenant_id)


@router.get("/api/channels/inbound/sessions/{conversation_id}")
def get_inbound_session(
    conversation_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    items = _main.services_for(principal).inbound_sessions.list(principal.tenant_id, conversation_id)
    return {"conversationId": conversation_id, "items": items}


@router.get("/api/channels/{binding_id}/remote-webhooks")
def list_remote_webhooks(
    binding_id: str, principal: Principal = Depends(require("read"))
) -> dict[str, Any]:
    """渠道令牌失效不污染端点契约：200 空 items + error（binding error 态已由 registry 落库）。"""
    registry = _main.services_for(principal).channel_registry
    try:
        items = registry.remote_webhooks(binding_id)
    except ChannelError as exc:
        if exc.code in CHANNEL_AUTHORIZATION_ERROR_CODES:
            return {"items": [], "error": CHANNEL_UPSTREAM_UNAUTHORIZED}
        raise _channel_http_error(exc)
    return {"items": items}


@router.post("/api/channels/{binding_id}/remote-webhooks", status_code=201)
def register_remote_webhook(
    binding_id: str,
    body: RemoteWebhookRequest,
    http_request: Request,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    services = _main.services_for(principal)
    try:
        item = services.channel_registry.register_remote(binding_id, body.topic)
    except ChannelError as exc:
        _record_channel_audit(
            services, principal, http_request, "channel.webhook.register",
            exc.status_code,
        )
        raise _channel_http_error(exc)
    _record_channel_audit(services, principal, http_request, "channel.webhook.register", 201)
    return item


@router.delete("/api/channels/{binding_id}/remote-webhooks/{topic:path}")
def unregister_remote_webhook(
    binding_id: str,
    topic: str,
    http_request: Request,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    """幂等：未注册返回 {deleted:false}。"""
    services = _main.services_for(principal)
    try:
        deleted = services.channel_registry.unregister_remote(binding_id, topic)
    except ChannelError as exc:
        _record_channel_audit(
            services, principal, http_request, "channel.webhook.unregister",
            exc.status_code,
        )
        raise _channel_http_error(exc)
    _record_channel_audit(
        services, principal, http_request, "channel.webhook.unregister", 200
    )
    return {"deleted": deleted}
