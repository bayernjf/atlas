"""自然语言 → 流程草稿（docs/08 §7.3 验收 6；REST POST /api/nl/generate）。

配置 LITELLM_MODEL 时由 LLM 产出 Graph JSON（version 1）；
未配置或模型输出无法解析时，用退款意图规则模板兜底，
保证 Demo 离线可用。返回值为可直接回显画布的 SerializedGraph 字典。
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

_REFUND_KEYWORDS = ("退款", "退货", "售后")


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


def generate_graph(prompt: str) -> dict[str, Any]:
    model = os.getenv("LITELLM_MODEL", "").strip()
    if model:
        generated = _generate_with_llm(prompt, model)
        if generated is not None:
            return generated
    if any(keyword in prompt for keyword in _REFUND_KEYWORDS):
        return refund_template_graph()
    raise ValueError("未能识别流程意图（规则兜底仅支持退款/售后场景；配置 LITELLM_MODEL 可支持任意描述）")


def _generate_with_llm(prompt: str, model: str) -> dict[str, Any] | None:
    import litellm

    system = (
        "你是 Atlas 流程编排助手。把用户的中文需求转成 Graph JSON（只输出 JSON，不要解释）。"
        "结构：{\"version\":1,\"variables\":[{\"name\",\"type\",\"value\",\"scope\":\"global\"}],"
        "\"nodes\":[{\"id\",\"type\"(trigger/ai_decision/tool_call),\"name\",\"description\","
        "\"position\":{\"x\",\"y\"},\"config\":{...},\"retry\":{\"max_retries\":0,"
        "\"backoff\":\"1s\",\"timeout\":30,\"on_error\":\"stop\"}}],\"edges\":[{\"id\",\"source\",\"target\"}]}。"
        "Demo 仅支持 trigger/ai_decision/tool_call 三类节点，流程从触发器开始。"
    )
    response = litellm.completion(
        model=model,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        temperature=0.2,
    )
    content = response["choices"][0]["message"]["content"]
    match = re.search(r"\{.*\}", content, re.DOTALL)
    if not match:
        return None
    try:
        graph = json.loads(match.group(0))
        graph["version"] = 1
        return graph
    except json.JSONDecodeError:
        return None
