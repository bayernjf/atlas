"""后端元数据多语言（D13 后端切片，docs/70）。

零新依赖 / 零迁移 / 零新端点：翻译集中在本文，canonical 仍是各实体的 zh-CN 串，
端点只在返回前按 locale 换值。locale 取自 ``Accept-Language`` 首档，fail-closed 回退 zh-CN。

约定（docs/70 §2）：
- 模板 ``name``/``description`` 均本地化（``id`` 是独立稳定键）。
- 适配器只本地化 ``description``；``name`` 是 wire 标识符（前端 ``adapter_id/tool.name``＝``config.tool``），**禁止本地化**。
- ``tags`` 不翻译；专名（Shopify/PostgreSQL 等）保留原文。
"""

from __future__ import annotations

from typing import Any

SUPPORTED_LOCALES = ("zh-CN", "en-US")
DEFAULT_LOCALE = "zh-CN"

# template_id -> { "en-US": { "name": ..., "description": ... } }
TEMPLATE_I18N: dict[str, dict[str, dict[str, str]]] = {
    "refund-auto": {
        "en-US": {
            "name": "Auto Refund Approval",
            "description": "E-commerce refund golden flow: AI decides by reason and amount; "
            "auto-refunds within limit, routes to human review above limit.",
        }
    },
    "http-orders-branch": {
        "en-US": {
            "name": "HTTP Order Fetch + Conditional Branch",
            "description": "Generic HTTP fetch of demo orders; branch by HTTP status code to "
            "send a success notification or a failure alert.",
        }
    },
    "sql-query-notify": {
        "en-US": {
            "name": "SQL Query + Conditional Notification",
            "description": "Query demo order DB by threshold (bound parameter); notify ops on "
            "large orders, else send a routine inspection message. Runtime input: min_amount.",
        }
    },
    "sql-approval-write": {
        "en-US": {
            "name": "SQL Write After Approval",
            "description": "Write operation requires human approval first: execute UPDATE on "
            "approval; on rejection (incl. timeout) send a notification. Runtime input: order_id.",
        }
    },
    "approval-timeout-reject": {
        "en-US": {
            "name": "Approval Timeout Auto-Reject Demo",
            "description": "Human-in-the-loop node auto-rejects if not approved within 10s, "
            "branching to approval or rejection notification paths.",
        }
    },
}

# 能力 description(zh-CN) -> { "en-US": description }（能力 ``name`` 是短 id/wire id，不在此列；
# 动态能力无条目，fail-safe 回退 canonical 中文原文）
CAPABILITY_I18N: dict[str, dict[str, str]] = {
    # web-playwright
    "打开指定 URL": {"en-US": "Open the specified URL"},
    "按描述或选择器点击页面元素": {"en-US": "Click a page element by description or selector"},
    "在输入框中填写文本": {"en-US": "Fill text into an input field"},
    "对当前页面截图": {"en-US": "Take a screenshot of the current page"},
    # database
    "通用 SQL 只读查询，返回结果集摘要（超 limit 截断）": {
        "en-US": "Generic read-only SQL query returning a result summary (truncated beyond limit)"
    },
    "通用 SQL 写操作（INSERT/UPDATE/DELETE/DDL），返回影响行数": {
        "en-US": "Generic SQL write (INSERT/UPDATE/DELETE/DDL) returning affected row count"
    },
    # message
    "发送消息：email/webhook/dingtalk/wecom/feishu 真实投递（IM 与 webhook 单 URL、过 SSRF 校验），其余渠道仅进程内记录；群发上限 20": {
        "en-US": "Send a message: real delivery via email/webhook/dingtalk/wecom/feishu "
        "(IM & webhook single URL, SSRF-checked); other channels recorded in-process only; max 20 per send"
    },
    # memory
    "记住一条长期事实（fact）或用户偏好（preference），供后续运行语义检索": {
        "en-US": "Store a long-term fact or user preference for later semantic recall"
    },
    "按自然语言/关键词语义检索本租户长期记忆，返回最相关的若干条（无命中返空）": {
        "en-US": "Semantic search over this tenant's long-term memory by natural language or "
        "keywords, returning the most relevant entries (empty if none)"
    },
    # shop
    "登录商家售后控制台": {"en-US": "Log in to the merchant after-sales console"},
    "获取待处理退款单列表": {"en-US": "Fetch the list of pending refund requests"},
    "对指定订单执行退款（资金操作）": {
        "en-US": "Execute a refund for the specified order (financial operation)"
    },
    "对指定订单发起人工审批": {"en-US": "Request human approval for the specified order"},
    "按上游 AI 决策动作执行退款或转人工审批": {
        "en-US": "Execute refund or route to human approval per the upstream AI decision"
    },
    # channel:shopify
    "列出 Shopify 店铺订单（open/any/closed）": {
        "en-US": "List Shopify store orders (open/any/closed)"
    },
    "按 id 获取 Shopify 订单详情": {"en-US": "Get Shopify order details by id"},
    "对 Shopify 订单发起退款（金额≤订单总额）": {
        "en-US": "Issue a refund for a Shopify order (amount ≤ order total)"
    },
    # httpapi
    "发起通用 HTTP 请求（任何 HTTP 响应均为成功，按 status 判分支）": {
        "en-US": "Make a generic HTTP request (any HTTP response is a success; branch by status)"
    },
}


def resolve_locale(accept_language: str | None) -> str:
    """从 ``Accept-Language`` 取首档，匹配 zh-CN/en-US，否则回退默认（fail-closed）。"""
    if not accept_language:
        return DEFAULT_LOCALE
    for part in accept_language.split(","):
        lang = part.split(";")[0].strip().replace("_", "-").lower()
        if lang in SUPPORTED_LOCALES:
            return lang
    first = accept_language.split(",")[0].split(";")[0].strip().replace("_", "-").lower()
    if first.startswith("zh"):
        return "zh-CN"
    if first.startswith("en"):
        return "en-US"
    return DEFAULT_LOCALE


def localize_template(meta: dict[str, Any], locale: str) -> dict[str, Any]:
    """按 locale 替换模板 ``name``/``description``；zh-CN 或缺失条目原样返回。"""
    if locale == "zh-CN":
        return meta
    entry = TEMPLATE_I18N.get(meta.get("id", ""), {}).get("en-US")
    if not entry:
        return meta
    return {**meta, "name": entry["name"], "description": entry["description"]}


def localize_tool_desc(tool: dict[str, Any], locale: str) -> dict[str, Any]:
    """按 locale 替换能力 ``description``；``name`` 是 wire id 不本地化。zh-CN 或缺失条目原样返回。

    以 ``description``(zh-CN 原文) 为键查表：能力 ``name`` 是短 id（如 ``login``），
    既非中文也非稳定翻译键，绝不能作本地化键（否则 en-US 全量 fail-safe 不翻译）。
    """
    if locale == "zh-CN":
        return tool
    entry = CAPABILITY_I18N.get(tool.get("description", ""), {}).get("en-US")
    if not entry:
        return tool
    return {**tool, "description": entry}
