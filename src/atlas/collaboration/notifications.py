# -*- coding: utf-8 -*-
"""审批挂起通知（docs/35 §2，T2；docs/14 D20 邮件子集）。

human_approval 节点登记 pending 后，由 graph 运行时旁路调用 notifier 通知收件人。
通知是**旁路能力**：调用方（graph/loader._register_approval）必须 fail-safe 吞掉一切
异常，绝不阻断图执行、不改变审批语义。

v1 仅做挂起通知邮件（纯文本，只给应用入口 URL，**不含一键决策链接**——邮件 GET 决策
落地页属 D33 余部，缓做）；决策结果通知申请人本批不做（无申请人邮箱字段）。
"""

from __future__ import annotations

from typing import Any, Protocol

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
    """复用 MessageService（email 渠道）发送纯文本挂起通知。

    public_url 在构造期注入（ATLAS_PUBLIC_URL），是应用入口（前端地址），
    邮件中只引导审批人前往应用，不内置决策链接。
    """

    def __init__(self, message_service: Any, public_url: str = DEFAULT_PUBLIC_URL) -> None:
        self._messages = message_service
        self._public_url = (public_url or DEFAULT_PUBLIC_URL).rstrip("/")

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
        lines.append(f"请前往应用查看并处理：{self._public_url}")
        lines.append("（本邮件仅为通知，不包含一键决策链接，请在应用内完成审批）")
        body = "\n".join(lines)
        # MessageService 负责真实 SMTP 投递或进程内记录（demo 回退）；
        # 投递失败会抛 MessageSendError，由 graph 调用方 fail-safe 捕获。
        self._messages.send("email", list(recipients), subject, body)
