# -*- coding: utf-8 -*-
"""审批挂起通知（docs/35 §2，T2；docs/14 D20 邮件子集）。

human_approval 节点登记 pending 后，由 graph 运行时旁路调用 notifier 通知收件人。
通知是**旁路能力**：调用方（graph/loader._register_approval）必须 fail-safe 吞掉一切
异常，绝不阻断图执行、不改变审批语义。

v1 仅做挂起通知邮件：邮件内附**一键决策深链**（签名 capability token，docs/36 §3）；
决策结果通知申请人本批不做（无申请人邮箱字段）。
"""

from __future__ import annotations

from typing import Any, Protocol

from .email_token import TokenIssuer

DEFAULT_PUBLIC_URL = "http://localhost:5174"


class ApprovalNotifier(Protocol):
    """审批挂起通知器；实现方须保证方法签名一致。"""

    def notify_pending(
        self,
        *,
        graph_id: str,
        node_id: str,
        token: str,
        summary: str,
        approver: str,
        timeout_seconds: int,
        recipients: list[str],
    ) -> None: ...


class EmailApprovalNotifier:
    """复用 MessageService（email 渠道）发送挂起通知，内含一键决策链接。

    tenant_id / issuer 在构造期注入；public_url 是应用入口（前端地址），决策链接为
    `{public_url}/approvals/{signed_token}`。
    """

    def __init__(
        self,
        message_service: Any,
        public_url: str = DEFAULT_PUBLIC_URL,
        tenant_id: str = "",
        issuer: TokenIssuer | None = None,
    ) -> None:
        self._messages = message_service
        self._public_url = (public_url or DEFAULT_PUBLIC_URL).rstrip("/")
        self._tenant_id = tenant_id
        self._issuer = issuer or TokenIssuer()

    def notify_pending(
        self,
        *,
        graph_id: str,
        node_id: str,
        token: str,
        summary: str,
        approver: str,
        timeout_seconds: int,
        recipients: list[str],
    ) -> None:
        title = summary or node_id
        subject = f"[Atlas] 审批待处理：{title}"
        signed = self._issuer.issue(self._tenant_id, token, timeout_seconds)
        decision_url = f"{self._public_url}/approvals/{signed}"
        lines = [
            "有一笔人机审批正在等待处理。",
            "",
            f"审批节点：{node_id}（图 {graph_id}）",
            f"审批说明：{summary or '（无）'}",
        ]
        if approver:
            lines.append(f"指定审批人：{approver}")
        lines.append(f"超时时间：{timeout_seconds} 秒（超时后按图中配置自动处理）")
        lines.append("")
        lines.append(f"一键处理：{decision_url}")
        lines.append(f"或前往应用：{self._public_url}")
        body = "\n".join(lines)
        # MessageService 负责真实 SMTP 投递或进程内记录（demo 回退）；
        # 投递失败会抛 MessageSendError，由 graph 调用方 fail-safe 捕获。
        self._messages.send("email", list(recipients), subject, body)
