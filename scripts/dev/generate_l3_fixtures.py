"""生成前端 L3 预判与后端 dsl.py 的对拍夹具（M4 批 2 ⑥ / U41）。

用法：.venv/bin/python scripts/dev/generate_l3_fixtures.py
输出：frontend/src/lib/validation/__tests__/l3-fixtures.json

每个用例以后端 parse_graph 实跑结果为唯一标准：
- structural：图通过了更早的校验阶段，最终错误只含不可达/非法环两类 L3 消息；
- raises-early：后端在结构校验之前即拒绝（前端预判域不覆盖，前端期望在 JSON 中
  单独以 frontendEarlyExpectation 记录并注明偏差理由）。
前后端测试（pytest + vitest）读同一份 JSON，规则改了重跑本脚本即可重新锁定。
"""

from __future__ import annotations

import json
from pathlib import Path

from atlas.graph.dsl import GraphValidationError, parse_graph

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = REPO_ROOT / "frontend" / "src" / "lib" / "validation" / "__tests__" / "l3-fixtures.json"

UNREACHABLE_MARK = "不可达（没有任何入边路径能到达它）"
CYCLE_MARK = "检测到非法循环依赖（循环只允许经循环节点的循环体回到自身）"


def retry() -> dict:
    return {"max_retries": 0, "backoff": "1s", "timeout": 30, "on_error": "stop"}


def trigger(node_id: str = "trigger-1", trigger_type: str = "webhook") -> dict:
    config = {"triggerType": trigger_type, "cron": "", "webhookUrl": ""}
    if trigger_type == "webhook":
        config["webhookUrl"] = "/hooks/refund"
    return {
        "id": node_id, "type": "trigger", "name": "触发", "position": {"x": 0, "y": 0},
        "config": config, "retry": retry(),
    }


def tool(node_id: str, name: str = "工具") -> dict:
    return {
        "id": node_id, "type": "tool_call", "name": name, "position": {"x": 0, "y": 0},
        "config": {"tool": "shop/process_refund", "params": ""}, "retry": retry(),
    }


def ai(node_id: str, name: str = "决策") -> dict:
    return {
        "id": node_id, "type": "ai_decision", "name": name, "position": {"x": 0, "y": 0},
        "config": {"promptTemplate": "处理 {{global.approval_limit}}", "confidenceThreshold": 0.6},
        "retry": retry(),
    }


def loop(node_id: str, body_target: str, exit_target: str, name: str = "循环") -> dict:
    return {
        "id": node_id, "type": "loop", "name": name, "position": {"x": 0, "y": 0},
        "config": {
            "mode": "while",
            "continueExpression": "{{%s.index}} < 3" % node_id,
            "maxIterations": 10,
            "bodyTarget": body_target,
            "exitTarget": exit_target,
        },
        "retry": retry(),
    }


def edge(source: str, target: str, eid: str | None = None) -> dict:
    return {"id": eid or f"e-{source}-{target}", "source": source, "target": target}


def graph(nodes: list[dict], edges: list[dict], variables: list[dict] | None = None) -> dict:
    return {
        "version": 1,
        "variables": variables
        if variables is not None
        else [{"name": "approval_limit", "type": "number", "value": "500", "scope": "global"}],
        "nodes": nodes,
        "edges": edges,
    }


CASES: dict[str, dict] = {
    "valid-linear": graph(
        [trigger(), ai("ai_decision-1"), tool("tool_call-1", "退款")],
        [edge("trigger-1", "ai_decision-1"), edge("ai_decision-1", "tool_call-1")],
        variables=[],
    ),
    "unreachable-one": graph(
        [trigger(), ai("ai_decision-1"), tool("tool_call-1", "退款"), tool("tool-orphan", "孤立")],
        [edge("trigger-1", "ai_decision-1"), edge("ai_decision-1", "tool_call-1")],
        variables=[],
    ),
    "unreachable-two": graph(
        [
            trigger(), ai("ai_decision-1"), tool("tool_call-1", "退款"),
            tool("tool-orphan-a", "孤立甲"), tool("tool-orphan-b", "孤立乙"),
        ],
        [edge("trigger-1", "ai_decision-1"), edge("ai_decision-1", "tool_call-1")],
        variables=[],
    ),
    "legal-loop-single-body": graph(
        [trigger(), loop("loop-1", "tool-body", "tool-exit"), tool("tool-body", "循环体"), tool("tool-exit", "退出")],
        [
            edge("trigger-1", "loop-1"),
            edge("loop-1", "tool-body"),
            edge("tool-body", "loop-1"),
            edge("loop-1", "tool-exit"),
        ],
        variables=[],
    ),
    "legal-loop-chain-body": graph(
        [
            trigger(),
            loop("loop-1", "tool-body-a", "tool-exit"),
            tool("tool-body-a", "循环体甲"),
            tool("tool-body-b", "循环体乙"),
            tool("tool-exit", "退出"),
        ],
        [
            edge("trigger-1", "loop-1"),
            edge("loop-1", "tool-body-a"),
            edge("tool-body-a", "tool-body-b"),
            edge("tool-body-b", "loop-1"),
            edge("loop-1", "tool-exit"),
        ],
        variables=[],
    ),
    "illegal-cycle-2": graph(
        [trigger(), tool("tool-x", "X"), tool("tool-y", "Y")],
        [
            edge("trigger-1", "tool-x"),
            edge("tool-x", "tool-y"),
            edge("tool-y", "tool-x"),
        ],
        variables=[],
    ),
    "illegal-cycle-self": graph(
        [trigger(), tool("tool-x", "X")],
        [edge("trigger-1", "tool-x"), edge("tool-x", "tool-x", "e-self")],
        variables=[],
    ),
    "illegal-cycle-3": graph(
        [
            trigger(),
            tool("tool-a", "A"),
            tool("tool-b", "B"),
            tool("tool-c", "C"),
            tool("tool-tail", "尾"),
        ],
        [
            edge("trigger-1", "tool-a"),
            edge("tool-a", "tool-b"),
            edge("tool-b", "tool-c"),
            edge("tool-c", "tool-a"),
            edge("tool-c", "tool-tail"),
        ],
        variables=[],
    ),
    "unreachable-and-cycle": graph(
        [
            trigger(),
            tool("tool-x", "X"),
            tool("tool-y", "Y"),
            tool("tool-orphan", "孤立"),
        ],
        [
            edge("trigger-1", "tool-x"),
            edge("tool-x", "tool-y"),
            edge("tool-y", "tool-x"),
        ],
        variables=[],
    ),
    # 后端更早拒绝的用例（前端预判域不覆盖，frontendEarlyExpectation 手写并注明理由）
    "no-trigger": graph(
        [tool("tool-a", "甲"), tool("tool-b", "乙")],
        [edge("tool-a", "tool-b")],
        variables=[],
    ),
    "loop-empty-body-target": graph(
        [
            trigger(),
            loop("loop-1", "", "tool-exit"),
            tool("tool-body", "循环体"),
            tool("tool-exit", "退出"),
        ],
        [
            edge("trigger-1", "loop-1"),
            edge("loop-1", "tool-body"),
            edge("tool-body", "loop-1"),
            edge("loop-1", "tool-exit"),
        ],
        variables=[],
    ),
}

# 后端在当前收集器实现下会跑完全部校验：bodyTarget 缺失等配置问题与结构问题同批返回，
# 结构域对拍只过滤两条 L3 消息（其余配置问题不属前端 l3.ts 两条规则范围）。
FRONTEND_EARLY_EXPECTATION: dict[str, dict] = {}


def structural_messages(messages: list[str]) -> list[str]:
    return [m for m in messages if UNREACHABLE_MARK in m or CYCLE_MARK in m]


def build_case(name: str, raw: dict) -> dict:
    try:
        parse_graph(raw)
        backend = {"structural": True, "messages": []}
    except GraphValidationError as exc:
        structural = structural_messages(exc.errors)
        if structural:
            backend = {"structural": True, "messages": structural}
        else:
            if name in FRONTEND_EARLY_EXPECTATION:
                backend = {
                    "structural": False,
                    "raisesWith": exc.errors[0],
                    "frontendEarlyExpectation": FRONTEND_EARLY_EXPECTATION[name],
                }
            else:
                raise SystemExit(
                    f"用例 {name} 未进入结构校验且未登记早拒期望，首批错误：{exc.errors[0]}"
                )
    return {
        "name": name,
        "nodes": [
            {"id": n["id"], "type": n["type"], "config": n.get("config", {})}
            for n in raw["nodes"]
        ],
        "edges": [{"source": e["source"], "target": e["target"]} for e in raw["edges"]],
        "backend": backend,
    }


def main() -> None:
    cases = [build_case(name, raw) for name, raw in CASES.items()]
    payload = {
        "_comment": (
            "M4 批 2 ⑥ L3 对拍夹具；由 scripts/dev/generate_l3_fixtures.py 以后端 "
            "parse_graph 实跑生成，勿手改 structural 期望值；规则变更后重跑生成器。"
        ),
        "cases": cases,
    }
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for case in cases:
        backend = case["backend"]
        if backend["structural"]:
            print(f"{case['name']}: structural messages={backend['messages']}")
        else:
            print(f"{case['name']}: raises-early ({backend['raisesWith']})")
    print(f"wrote {OUT_PATH.relative_to(REPO_ROOT)} ({len(cases)} cases)")


if __name__ == "__main__":
    main()
