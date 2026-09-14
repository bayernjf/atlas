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
        "\"nodes\":[{\"id\",\"type\"(trigger/ai_decision/tool_call/condition/loop/parallel),\"name\",\"description\","
        "\"position\":{\"x\",\"y\"},\"config\":{...},\"retry\":{\"max_retries\":0,"
        "\"backoff\":\"1s\",\"timeout\":30,\"on_error\":\"stop\"}}],\"edges\":[{\"id\",\"source\",\"target\"}]}。"
        "节点支持 trigger/ai_decision/tool_call/condition/loop/parallel 六类，流程从触发器开始。"
        "condition 节点的 config 为 {\"branches\":[{\"label\",\"expression\",\"target\"}],"
        "\"defaultTarget\"}：branches 按顺序短路，expression 仅支持 {{路径}} 变量引用、"
        "比较运算（> >= < <= == !=）、逻辑运算（&& || !）、括号与数字/字符串/true/false/null 字面量，"
        "禁止算术与函数；每个 branch 的 target 与 defaultTarget 都必须是已存在的节点 id，"
        "且每个目标都要有对应 edge，defaultTarget 必填。"
        "loop 节点（v1 仅条件循环）的 config 为 {\"mode\":\"while\",\"continueExpression\","
        "\"maxIterations\":10,\"bodyTarget\",\"exitTarget\"}：continueExpression 语法同 condition 表达式，"
        "为真时进入/再次进入循环体；maxIterations 为 1-100 的整数；bodyTarget 与 exitTarget 必须是已存在的节点 id；"
        "循环体末端节点必须连一条回到该 loop 节点的回边，exitTarget 另连一条出边，"
        "loop 节点恰好两条出边且不允许嵌套循环。"
        "parallel 节点（并行扇出/汇聚）的 config 为 {\"joinStrategy\",\"branches\":"
        "[{\"label\",\"target\"}],\"joinTarget\"}：joinStrategy 仅支持 all_success 或 all_completed；"
        "all_success 表示任一分支失败则汇聚状态 failed（汇聚节点仍执行），"
        "all_completed 表示各分支都走到汇聚即成功；branches 为 2-10 个，label 非空且不重复，"
        "target 必须是已存在且互不相同的节点 id；joinTarget 必须是已存在的节点 id 且不等于任一分支 target；"
        "parallel 节点的出边数恰好等于分支数且目标就是各 branch target，不直连结束；"
        "每个分支沿其内部连线最终必须能到达 joinTarget（分支末端连到 joinTarget），"
        "分支之间不得交叉连线，parallel 区域内不得再嵌套 parallel 节点。"
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
