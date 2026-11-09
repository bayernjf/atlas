"""Approval decision + wait-event parsing, extracted from api/main.py (Tier0 review P2).

`apply_approval_decision` is the single application path shared by the
logged-in decision endpoint and the email deep-link endpoint (docs/36 §3, anti
drift). The email token issuer and the tenant registry peek are injected by
the caller: main.py owns the process singletons and tests patch them there.
HTTP-shaped failures still raise HTTPException (route contract unchanged).
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal

from fastapi import HTTPException

from atlas.cards import CardRenderError, get_card, map_action_output
from atlas.collaboration.email_token import EmailTokenError, TokenIssuer
from atlas.graph.dsl import valid_event_key
from atlas.services.error_safety import token_ref as _token_ref

logger = logging.getLogger(__name__)

MAX_WAIT_PAYLOAD_BYTES = 4096
MAX_WAIT_PAYLOAD_KEYS = 50

EMAIL_LINK_INVALID = "审批链接无效或已过期"


def parse_wait_event_key(body: dict[str, Any]) -> str:
    raw = body.get("eventKey")
    event_key = raw.strip() if isinstance(raw, str) else ""
    if not valid_event_key(event_key):
        raise HTTPException(
            status_code=422,
            detail={"code": "WAIT_EVENT_KEY_INVALID",
                    "message": "eventKey 必须是 1-128 位字母、数字及 :_- 组合"},
        )
    return event_key


def parse_wait_payload(body: dict[str, Any]) -> dict[str, Any]:
    raw = body.get("payload", {})
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise HTTPException(
            status_code=422,
            detail={"code": "WAIT_EVENT_PAYLOAD_NOT_OBJECT",
                    "message": "payload 必须是 JSON 对象"},
        )
    if len(raw) > MAX_WAIT_PAYLOAD_KEYS:
        raise HTTPException(
            status_code=422,
            detail={"code": "WAIT_EVENT_PAYLOAD_TOO_MANY_KEYS",
                    "message": f"payload 顶层键不能超过 {MAX_WAIT_PAYLOAD_KEYS} 个",
                    "params": {"max": MAX_WAIT_PAYLOAD_KEYS}},
        )
    serialized = json.dumps(raw, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > MAX_WAIT_PAYLOAD_BYTES:
        raise HTTPException(
            status_code=422,
            detail={"code": "WAIT_EVENT_PAYLOAD_TOO_LARGE",
                    "message": f"payload 序列化后不能超过 {MAX_WAIT_PAYLOAD_BYTES} 字节",
                    "params": {"max": MAX_WAIT_PAYLOAD_BYTES}},
        )
    return raw


def apply_approval_decision(
    broker: Any,
    pending: dict[str, Any],
    token: str,
    *,
    decision: Literal["approved", "rejected"] | None,
    comment: str,
    action_id: str | None,
    form: dict[str, Any] | None,
    notifier: Any = None,
    resolved_by: str = "human",
) -> dict[str, Any]:
    """登录态决策与邮件深链决策共用的唯一应用函数（docs/36 §3，防双路漂移）。"""
    if pending.get("decision") is not None:
        raise HTTPException(status_code=409, detail="该审批请求已有决策，重复提交不生效")

    resolved_action: str | None = None
    if action_id:
        # M8：卡片动作提交，服务端按 action.output 映射 decision/comment（map_action_output 唯一权威）。
        card_template_id = pending.get("cardTemplateId")
        card = get_card(card_template_id) if card_template_id else None
        if card is None:
            raise HTTPException(status_code=422, detail="该审批请求未配置交互卡片或卡片不存在")
        try:
            mapped = map_action_output(card, action_id, form)
        except CardRenderError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        decision = mapped["decision"]
        comment = mapped.get("comment", "")
        resolved_action = action_id
    elif decision is None:
        raise HTTPException(
            status_code=422,
            detail="请提供 decision（approved/rejected）或卡片 actionId",
        )

    if not broker.resolve(token, decision, comment=comment, action_id=resolved_action):
        raise HTTPException(status_code=409, detail="该审批请求已有决策，重复提交不生效")
    # docs/37 §4：决策结果邮件（旁路 fail-safe，不影响响应与决策事实）。
    if notifier is not None:
        recipients = broker.get_notify_recipients(token)
        if recipients:
            try:
                notifier.notify_decided(
                    graph_id=pending.get("graph_id", ""),
                    node_id=pending.get("node_id", ""),
                    summary=pending.get("summary", ""),
                    decision=decision,
                    resolved_by=resolved_by,
                    comment=comment,
                    recipients=recipients,
                )
            except Exception as exc:  # noqa: BLE001 结果通知任何异常都不改变响应
                logger.warning("审批结果通知失败 token=%s: %s", _token_ref(token), exc)
    result: dict[str, Any] = {"token": token, "decision": decision, "resolvedBy": "human"}
    if resolved_action:
        result["actionId"] = resolved_action
    return result


def resolve_email_signed(
    signed: str,
    *,
    token_issuer: TokenIssuer,
    peek_registry: Any,
) -> tuple[Any, str, str]:
    """验签 → peek 已装配租户；任何失败统一 404，不区分原因。

    `token_issuer` 是 main 持有的 HMAC 单例；`peek_registry` 是
    `tenant_registry.peek` 的当前值（测试可 patch main.tenant_registry）。
    """
    try:
        body = token_issuer.verify(signed)
    except EmailTokenError as exc:
        raise HTTPException(status_code=404, detail=EMAIL_LINK_INVALID) from exc
    tenant_id = str(body.get("tenant", ""))
    services = peek_registry(tenant_id)
    if services is None:
        raise HTTPException(status_code=404, detail=EMAIL_LINK_INVALID)
    if body.get("rcpt") is None:
        # docs/64 J-1b：无收件人绑定的旧式 token 一律失效（TTL 仅 1h+grace，不向后兼容）。
        raise HTTPException(status_code=404, detail=EMAIL_LINK_INVALID)
    return services, str(body.get("at", "")), tenant_id


def assert_email_token_recipient(
    signed: str, approval_token: str, broker: Any, *, token_issuer: TokenIssuer
) -> None:
    """docs/64 J-1b：token 载荷 rcpt 必须属于该审批的收件人，否则 404（与验签同口径）。"""
    try:
        body = token_issuer.verify(signed)
    except EmailTokenError as exc:
        raise HTTPException(status_code=404, detail=EMAIL_LINK_INVALID) from exc
    rcpt = str(body.get("rcpt", "")).lower()
    recipients = {r.lower() for r in broker.get_notify_recipients(approval_token)}
    if rcpt not in recipients:
        raise HTTPException(status_code=404, detail=EMAIL_LINK_INVALID)
