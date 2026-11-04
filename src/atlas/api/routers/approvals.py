"""P2-1 split: Approval inbox, decisions, email decisions and approval cards.

Handlers are moved verbatim from atlas/api/main.py; shared helpers and
pydantic models stay in main and are imported back here so the route
surface (method, path, signature, dependencies) is unchanged.
"""

from __future__ import annotations

from fastapi import APIRouter

import atlas.api.main as _main
import time
from atlas.cards import CardRenderError, get_card, list_cards, render_card
from atlas.collaboration.notifications import EmailApprovalNotifier
from atlas.iam.deps import require
from atlas.iam.principals import Principal
from fastapi import Depends, HTTPException, Request
from typing import Any, Literal
from atlas.api.main import (
    ApprovalDecisionRequest,
    EmailDecisionRequest,
    _EMAIL_LINK_INVALID,
    _PUBLIC_URL,
    _apply_approval_decision,
    _assert_email_token_recipient,
    _resolve_email_signed,
    logger,
)


router: APIRouter = APIRouter()


@router.get("/api/cards")
def list_catalog_cards(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """列出内置交互卡片目录（M8；只读代码常量，不受 reset 影响，12 §3.11）。"""
    return {"items": [card.model_dump() for card in list_cards()]}


@router.get("/api/approvals/{token}/card")
def render_approval_card(
    token: str,
    channel: Literal["web", "im", "email"] = "web",
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """审批卡片按渠道渲染（M8，viewer+）；只读，不产生决策副作用（防邮件预取）。

    未知 token 404、该审批未配卡片 404、非法 channel 由 FastAPI 判 422。
    渲染上下文取挂起时快照（中断恢复后从帧重建）。
    """
    broker = _main.services_for(principal).approval_broker
    pending = broker.get(token)
    if pending is None:
        raise HTTPException(status_code=404, detail=f"审批请求不存在或已清理：{token}")
    card_template_id = pending.get("cardTemplateId")
    if not card_template_id:
        raise HTTPException(status_code=404, detail="该审批请求未配置交互卡片")
    card = get_card(card_template_id)
    if card is None:  # 理论不发生：编译期已校验目录命中
        raise HTTPException(status_code=404, detail=f"交互卡片不存在：{card_template_id}")
    try:
        return render_card(
            card,
            broker.get_card_context(token),
            token=token,
            channel=channel,
            approver=pending.get("approver", ""),
            timeout_seconds=pending.get("timeoutSeconds"),
        )
    except CardRenderError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/api/approvals")
def list_approvals(
    principal: Principal = Depends(require("read")),
) -> dict[str, list[dict[str, Any]]]:
    """列出当前租户 pending 的人工审批请求（进程内 broker，重启即失）。"""
    return {"items": _main.services_for(principal).approval_broker.list_pending()}


@router.get("/api/approvals/decided")
def list_decided_approvals(
    limit: int = 50,
    principal: Principal = Depends(require("read")),
) -> dict[str, Any]:
    """列出当前租户已决策的人工审批（最近处理在前，进程内、reset/重启即失）。"""
    bounded = max(1, min(int(limit), 200))
    items = _main.services_for(principal).approval_broker.list_decided(bounded)
    return {"items": items, "limit": bounded}


@router.post("/api/approvals/{token}/decision")
def decide_approval(
    token: str,
    request: ApprovalDecisionRequest,
    principal: Principal = Depends(require("operate")),
) -> dict[str, Any]:
    broker = _main.services_for(principal).approval_broker
    pending = broker.get(token)
    if pending is None:
        # 跨租户 token 同样 404，不泄漏存在性（04 §5.14）
        raise HTTPException(status_code=404, detail=f"审批请求不存在或已清理：{token}")
    # 打包 A4（docs/100 §3）：指派校验——approver 非空时决策者必须匹配。
    # user:<id> → 与 principal.username 精确匹配（iam v1 用户标识即 username，无独立 user_id）；
    # 邮箱形态（含 @）→ 与 principal 邮箱匹配（iam 现状无 email 字段 → 恒不匹配 403，
    # 待真实用户模型引入 email 后自然生效）；其他形态 → 403（不泄漏存在性差异）。
    # 空 approver → 任何人可决（现状逐字不变）。邮件深链端点（email-decision）无 principal，不走本校验。
    pending_approver = str(pending.get("approver") or "")
    if pending_approver:
        if pending_approver.startswith("user:"):
            assigned = principal.username == pending_approver[len("user:"):]
        elif "@" in pending_approver:
            assigned = False  # iam 无 email 字段（docs/100 §7 注记照实）
        else:
            assigned = False
        if not assigned:
            raise HTTPException(
                status_code=403,
                detail={"code": "APPROVAL_NOT_ASSIGNED",
                        "message": "该审批仅限指定审批人处理"},
            )
    notifier = EmailApprovalNotifier(
        _main.services_for(principal).message_service, _PUBLIC_URL, principal.tenant_id
    )
    return _apply_approval_decision(
        broker,
        pending,
        token,
        decision=request.decision,
        comment=request.comment,
        action_id=request.action_id,
        form=request.form,
        notifier=notifier,
        resolved_by="human",
    )


@router.get("/api/approvals/email-view")
def email_approval_view(token: str) -> dict[str, Any]:
    """邮件深链只读视图（docs/36 §3）：无需登录、无副作用（防邮件预取）。"""
    services, approval_token, _ = _resolve_email_signed(token)
    broker = services.approval_broker
    pending = broker.get(approval_token)
    if pending is None:
        raise HTTPException(status_code=404, detail=_EMAIL_LINK_INVALID)
    _assert_email_token_recipient(token, approval_token, broker)
    now = time.time()
    remaining = max(
        0.0, float(pending.get("createdAt", 0.0)) + float(pending["timeoutSeconds"]) - now
    )
    view: dict[str, Any] = {
        "status": "resolved" if pending.get("decision") else "pending",
        "summary": pending.get("summary", ""),
        "nodeId": pending.get("node_id", ""),
        "graphId": pending.get("graph_id", ""),
        "approver": pending.get("approver", ""),
        "timeoutSeconds": pending["timeoutSeconds"],
        "createdAt": pending.get("createdAt", 0),
        "remainingSeconds": int(remaining),
    }
    if pending.get("decision"):
        view["decision"] = pending["decision"]
        view["resolvedBy"] = pending.get("resolvedBy")
    card_template_id = pending.get("cardTemplateId")
    if card_template_id:
        card = get_card(card_template_id)
        if card is not None:
            view["card"] = render_card(
                card,
                broker.get_card_context(approval_token),
                token=approval_token,
                channel="email",
                approver=pending.get("approver", ""),
                timeout_seconds=pending.get("timeoutSeconds"),
            )
    return view


@router.post("/api/approvals/email-decision")
def email_approval_decision(
    request: EmailDecisionRequest, http_request: Request
) -> dict[str, Any]:
    """邮件深链提交决策（docs/36 §3）：无需登录，与登录态端点共用决策应用函数。"""
    services, approval_token, tenant_id = _resolve_email_signed(request.token)
    broker = services.approval_broker
    pending = broker.get(approval_token)
    if pending is None:
        raise HTTPException(status_code=404, detail=_EMAIL_LINK_INVALID)
    _assert_email_token_recipient(request.token, approval_token, broker)
    notifier = EmailApprovalNotifier(
        services.message_service, _PUBLIC_URL, tenant_id
    )
    result = _apply_approval_decision(
        broker,
        pending,
        approval_token,
        decision=request.decision,
        comment=request.comment,
        action_id=request.action_id,
        form=request.form,
        notifier=notifier,
        resolved_by="email-link",
    )
    client_ip = http_request.client.host if http_request.client else ""
    try:
        services.audit_store.record(
            tenant_id=tenant_id,
            actor="email-link",
            action=f"approval.email_decision:{result['decision']}",
            status_code=200,
            path="/api/approvals/email-decision",
            ip=client_ip,
        )
    except Exception as exc:  # 审计绝不阻断决策
        logger.warning("email decision audit record failed: %s", exc)
    return result
