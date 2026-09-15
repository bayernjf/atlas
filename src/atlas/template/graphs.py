"""内置流程模板的 Graph JSON（04 §5.10 权威契约）。

每个图都是标准 version 1 Graph JSON，必须过 atlas.graph.dsl 的全部静态/拓扑校验
（tests/test_templates.py U27 固化）；工具仅可引用 demo 注册表中的适配器。
节点 id 在模板内固定——「从模板新建」为整画布替换，不做 id 重映射。
"""

from __future__ import annotations

from typing import Any

_RETRY = {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"}


def _node(
    node_id: str,
    node_type: str,
    name: str,
    x: float,
    y: float,
    config: dict[str, Any],
) -> dict[str, Any]:
    return {
        "id": node_id,
        "type": node_type,
        "name": name,
        "description": "",
        "position": {"x": x, "y": y},
        "config": config,
        "retry": dict(_RETRY),
    }


def _edge(edge_id: str, source: str, target: str) -> dict[str, str]:
    return {"id": edge_id, "source": source, "target": target}


def refund_template_graph() -> dict[str, Any]:
    """电商退款审批三节点模板（与 01 §4.2 场景 A、06 §9.2 用例一致）。"""
    return {
        "version": 1,
        "variables": [
            {"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}
        ],
        "nodes": [
            {
                "id": "trigger-1",
                "type": "trigger",
                "name": "触发：新退款申请",
                "description": "",
                "position": {"x": 80, "y": 180},
                "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"},
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
            {
                "id": "ai_decision-1",
                "type": "ai_decision",
                "name": "AI 决策：退款还是人工",
                "description": "",
                "position": {"x": 360, "y": 180},
                "config": {
                    "promptTemplate": (
                        "退款单 {{trigger-1.context.payload.order_id}}："
                        "{{trigger-1.context.payload.reason}}，"
                        "金额 {{trigger-1.context.payload.amount}}，"
                        "审批限额 {{global.approval_limit}}"
                    ),
                    "model": "",
                    "confidenceThreshold": 0.6,
                },
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
            {
                "id": "tool_call-1",
                "type": "tool_call",
                "name": "工具：执行退款或转人工",
                "description": "",
                "position": {"x": 660, "y": 180},
                "config": {"tool": "shop/process_refund", "params": ""},
                "retry": {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"},
            },
        ],
        "edges": [
            {"id": "e-trigger-decision", "source": "trigger-1", "target": "ai_decision-1"},
            {"id": "e-decision-action", "source": "ai_decision-1", "target": "tool_call-1"},
        ],
    }


def http_orders_branch_graph() -> dict[str, Any]:
    """HTTP 拉单 + 按响应状态分流通知。"""
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            _node(
                "trigger-1",
                "trigger",
                "触发：定时拉取订单",
                80,
                200,
                {"triggerType": "webhook", "webhookUrl": "/hooks/orders"},
            ),
            _node(
                "http-1",
                "tool_call",
                "HTTP：拉取演示订单",
                340,
                200,
                {
                    "tool": "http/request",
                    "params": (
                        '{"method":"GET",'
                        '"url":"http://localhost:8000/api/demo/mock/orders",'
                        '"headers":{"X-Demo-Token":"demo-token"}}'
                    ),
                },
            ),
            _node(
                "condition-1",
                "condition",
                "条件：拉单是否成功",
                640,
                200,
                {
                    "branches": [
                        {
                            "label": "HTTP 200",
                            "expression": "{{http-1.result.status}} == 200",
                            "target": "notify-ok",
                        }
                    ],
                    "defaultTarget": "notify-fail",
                },
            ),
            _node(
                "notify-ok",
                "tool_call",
                "消息：拉单成功通知",
                940,
                80,
                {
                    "tool": "message/send",
                    "params": (
                        '{"channel":"email","to":["ops@example.com"],'
                        '"subject":"订单拉取成功（HTTP {{http-1.result.status}}）",'
                        '"body":"演示订单接口返回成功，可继续后续处理。"}'
                    ),
                },
            ),
            _node(
                "notify-fail",
                "tool_call",
                "消息：拉单失败告警",
                940,
                320,
                {
                    "tool": "message/send",
                    "params": (
                        '{"channel":"email","to":["ops@example.com"],'
                        '"subject":"订单拉取失败（HTTP {{http-1.result.status}}）",'
                        '"body":"演示订单接口未返回 200，请检查 token 或服务状态。"}'
                    ),
                },
            ),
        ],
        "edges": [
            _edge("e-trigger-http", "trigger-1", "http-1"),
            _edge("e-http-condition", "http-1", "condition-1"),
            _edge("e-condition-ok", "condition-1", "notify-ok"),
            _edge("e-condition-fail", "condition-1", "notify-fail"),
        ],
    }


def sql_query_notify_graph() -> dict[str, Any]:
    """SQL 查询大额订单 + 条件通知（运行时输入 min_amount）。"""
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            _node(
                "trigger-1",
                "trigger",
                "触发：大额订单巡检",
                80,
                200,
                {"triggerType": "webhook", "webhookUrl": "/hooks/large-orders"},
            ),
            _node(
                "query-1",
                "tool_call",
                "SQL：查询大额订单",
                340,
                200,
                {
                    "tool": "database/query",
                    "params": (
                        '{"sql":"SELECT order_id, amount FROM orders '
                        'WHERE amount > :min ORDER BY amount DESC",'
                        '"params":{"min":{{trigger-1.context.payload.min_amount}}},'
                        '"limit":50}'
                    ),
                },
            ),
            _node(
                "condition-1",
                "condition",
                "条件：是否存在大额订单",
                640,
                200,
                {
                    "branches": [
                        {
                            "label": "有大额订单",
                            "expression": "{{query-1.result.row_count}} > 0",
                            "target": "notify-yes",
                        }
                    ],
                    "defaultTarget": "notify-no",
                },
            ),
            _node(
                "notify-yes",
                "tool_call",
                "消息：大额订单通知",
                940,
                80,
                {
                    "tool": "message/send",
                    "params": (
                        '{"channel":"email","to":["ops@example.com"],'
                        '"subject":"发现 {{query-1.result.row_count}} 笔大额订单",'
                        '"body":"阈值 {{trigger-1.context.payload.min_amount}} 元，'
                        '请及时跟进审批。"}'
                    ),
                },
            ),
            _node(
                "notify-no",
                "tool_call",
                "消息：无大额订单",
                940,
                320,
                {
                    "tool": "message/send",
                    "params": (
                        '{"channel":"email","to":["ops@example.com"],'
                        '"subject":"无大额订单",'
                        '"body":"阈值 {{trigger-1.context.payload.min_amount}} 元内无订单，'
                        '巡检正常。"}'
                    ),
                },
            ),
        ],
        "edges": [
            _edge("e-trigger-query", "trigger-1", "query-1"),
            _edge("e-query-condition", "query-1", "condition-1"),
            _edge("e-condition-yes", "condition-1", "notify-yes"),
            _edge("e-condition-no", "condition-1", "notify-no"),
        ],
    }


def sql_approval_write_graph() -> dict[str, Any]:
    """写库操作先人工审批：通过后执行 SQL，拒绝则消息通知。"""
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            _node(
                "trigger-1",
                "trigger",
                "触发：退款写库申请",
                80,
                200,
                {"triggerType": "webhook", "webhookUrl": "/hooks/refund-apply"},
            ),
            _node(
                "approval-1",
                "human_approval",
                "审批：确认退款写库",
                360,
                200,
                {
                    "summary": (
                        "确认将订单 {{trigger-1.context.payload.order_id}} "
                        "在订单库中标记为已退款？"
                    ),
                    "approver": "",
                    "timeoutSeconds": 3600,
                    "onTimeout": "reject",
                    "approvedTarget": "write-1",
                    "rejectedTarget": "reject-msg",
                },
            ),
            _node(
                "write-1",
                "tool_call",
                "SQL：执行退款状态更新",
                680,
                80,
                {
                    "tool": "database/execute",
                    "params": (
                        '{"sql":"UPDATE orders SET status = :status '
                        'WHERE order_id = :id",'
                        '"params":{"status":"refunded",'
                        '"id":"{{trigger-1.context.payload.order_id}}"}}'
                    ),
                },
            ),
            _node(
                "reject-msg",
                "tool_call",
                "消息：审批驳回通知",
                680,
                320,
                {
                    "tool": "message/send",
                    "params": (
                        '{"channel":"email","to":["ops@example.com"],'
                        '"subject":"退款写库审批被驳回",'
                        '"body":"订单 '
                        '{{trigger-1.context.payload.order_id}} 的退款写库申请未通过。"}'
                    ),
                },
            ),
        ],
        "edges": [
            _edge("e-trigger-approval", "trigger-1", "approval-1"),
            _edge("e-approval-write", "approval-1", "write-1"),
            _edge("e-approval-reject", "approval-1", "reject-msg"),
        ],
    }


def approval_timeout_reject_graph() -> dict[str, Any]:
    """人工审批超时默认拒绝演示（10 秒不审批自动走拒绝分支）。"""
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            _node(
                "trigger-1",
                "trigger",
                "触发：加急审批",
                80,
                200,
                {"triggerType": "webhook", "webhookUrl": "/hooks/urgent-approval"},
            ),
            _node(
                "approval-1",
                "human_approval",
                "审批：10 秒超时演示",
                380,
                200,
                {
                    "summary": (
                        "加急审批演示：10 秒内不操作将自动拒绝。"
                        "订单 {{trigger-1.context.payload.order_id}}"
                    ),
                    "approver": "",
                    "timeoutSeconds": 10,
                    "onTimeout": "reject",
                    "approvedTarget": "approved-msg",
                    "rejectedTarget": "rejected-msg",
                },
            ),
            _node(
                "approved-msg",
                "tool_call",
                "消息：审批通过",
                720,
                80,
                {
                    "tool": "message/send",
                    "params": (
                        '{"channel":"email","to":["ops@example.com"],'
                        '"subject":"审批已通过",'
                        '"body":"订单 {{trigger-1.context.payload.order_id}} '
                        '的审批通过（{{approval-1.decision}}）。"}'
                    ),
                },
            ),
            _node(
                "rejected-msg",
                "tool_call",
                "消息：审批拒绝/超时",
                720,
                320,
                {
                    "tool": "message/send",
                    "params": (
                        '{"channel":"email","to":["ops@example.com"],'
                        '"subject":"审批已拒绝",'
                        '"body":"订单 {{trigger-1.context.payload.order_id}} '
                        '审批未通过（{{approval-1.decision}}）。"}'
                    ),
                },
            ),
        ],
        "edges": [
            _edge("e-trigger-approval", "trigger-1", "approval-1"),
            _edge("e-approval-approved", "approval-1", "approved-msg"),
            _edge("e-approval-rejected", "approval-1", "rejected-msg"),
        ],
    }
