"""A-06 真实 LLM 20 条 NL 草稿 + 技能 top-3 + L3 编译评估。

运行前把 `.env` 里的模型配置加载进来（脚本自带最小 .env 读取，不引新依赖）：

    .venv/bin/python scripts/eval_nl_skills.py --env-file .env

输出 `outputs/a06-nl-skills/20-case-eval.json`，失败用例保留完整 graph 与编译错误。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "outputs" / "a06-nl-skills" / "20-case-eval.json"

CASES: list[dict[str, Any]] = [
    {
        "id": "refund-basic",
        "prompt": "客户申请退款，先查询待处理退款单；金额超过1000转人工审批，否则自动退款；最后邮件通知客户。",
        "expectSkillAny": ["shop", "database"],
        "expectToolAny": ["shop/list_pending_refunds", "shop/process_refund", "shop/execute_refund", "database/query"],
        "requireNodeTypes": ["condition", "human_approval"],
    },
    {
        "id": "refund-auto-low",
        "prompt": "客户申请退款，金额小于200直接退款，不用人工审批。",
        "expectSkillAny": ["shop"],
        "expectToolAny": ["shop/execute_refund", "shop/process_refund"],
        "requireNodeTypes": [],
    },
    {
        "id": "refund-return-approval",
        "prompt": "退货退款必须先人工审批，审批通过后退款，拒绝则通知客户。",
        "expectSkillAny": ["shop", "message"],
        "expectToolAny": ["shop/request_human_approval", "shop/execute_refund", "shop/reject_refund"],
        "requireNodeTypes": ["human_approval"],
    },
    {
        "id": "refund-reject",
        "prompt": "客户退款申请不符合规则，拒绝退款并邮件说明原因。",
        "expectSkillAny": ["shop", "message"],
        "expectToolAny": ["shop/reject_refund", "message/send"],
        "requireNodeTypes": [],
    },
    {
        "id": "refund-status-query",
        "prompt": "按订单号查询退款进度，并把结果发邮件给客户。",
        "expectSkillAny": ["shop", "database", "message"],
        "expectToolAny": ["shop/list_pending_refunds", "database/query", "message/send"],
        "requireNodeTypes": [],
    },
    {
        "id": "refund-daily-report",
        "prompt": "每天统计退款金额并生成报表，发送给运营。",
        "expectSkillAny": ["database", "message", "shop"],
        "expectToolAny": ["database/query", "message/send"],
        "requireNodeTypes": [],
    },
    {
        "id": "refund-high-approval",
        "prompt": "订单金额超过5000时必须人工审批，审批通过才退款。",
        "expectSkillAny": ["shop", "database"],
        "expectToolAny": ["shop/request_human_approval", "shop/execute_refund", "database/query"],
        "requireNodeTypes": ["human_approval", "condition"],
    },
    {
        "id": "refund-success-email",
        "prompt": "退款成功后发邮件通知客户。",
        "expectSkillAny": ["message", "shop"],
        "expectToolAny": ["message/send", "shop/process_refund"],
        "requireNodeTypes": [],
    },
    {
        "id": "refund-fail-dingtalk",
        "prompt": "退款失败时用钉钉通知运营主管。",
        "expectSkillAny": ["message", "shop"],
        "expectToolAny": ["message/send"],
        "requireNodeTypes": [],
    },
    {
        "id": "erp-stock-http",
        "prompt": "调用 ERP 的 HTTP 接口查询库存，库存不足时通知运营。",
        "expectSkillAny": ["http", "message"],
        "expectToolAny": ["http/request", "message/send"],
        "requireNodeTypes": ["condition"],
    },
    {
        "id": "web-login-screenshot",
        "prompt": "打开商家后台网页，登录后截图保存。",
        "expectSkillAny": ["web-playwright"],
        "expectToolAny": ["web-playwright/navigate", "web-playwright/click", "web-playwright/type", "web-playwright/screenshot"],
        "requireNodeTypes": [],
    },
    {
        "id": "web-refund-form",
        "prompt": "在网页退款表单里填写订单号和退款原因并提交。",
        "expectSkillAny": ["web-playwright"],
        "expectToolAny": ["web-playwright/type", "web-playwright/click"],
        "requireNodeTypes": [],
    },
    {
        "id": "memory-preference",
        "prompt": "记住这位客户不要电话联系，只接受邮件。",
        "expectSkillAny": ["memory"],
        "expectToolAny": ["memory/remember"],
        "requireNodeTypes": [],
    },
    {
        "id": "memory-recall-channel",
        "prompt": "检索客户历史偏好，决定这次用什么渠道通知。",
        "expectSkillAny": ["memory", "message"],
        "expectToolAny": ["memory/recall", "message/send"],
        "requireNodeTypes": [],
    },
    {
        "id": "memory-auto-extract",
        "prompt": "从客服对话里自动提取客户偏好并保存。",
        "expectSkillAny": ["memory"],
        "expectToolAny": ["memory/auto_extract", "memory/remember"],
        "requireNodeTypes": [],
    },
    {
        "id": "batch-refund-summary",
        "prompt": "批量查询退款单并汇总金额，超过阈值转人工。",
        "expectSkillAny": ["shop", "database"],
        "expectToolAny": ["database/query", "shop/list_pending_refunds"],
        "requireNodeTypes": ["condition"],
    },
    {
        "id": "refund-retry",
        "prompt": "退款失败后重试一次，再失败就通知人工。",
        "expectSkillAny": ["shop", "message"],
        "expectToolAny": ["shop/execute_refund", "shop/process_refund", "message/send"],
        "requireNodeTypes": [],
    },
    {
        "id": "refund-feishu-finance",
        "prompt": "退款成功后发飞书通知财务。",
        "expectSkillAny": ["message", "shop"],
        "expectToolAny": ["message/send"],
        "requireNodeTypes": [],
    },
    {
        "id": "refund-double-approval",
        "prompt": "高额退款需要两级人工审批后再退款。",
        "expectSkillAny": ["shop"],
        "expectToolAny": ["shop/request_human_approval", "shop/execute_refund"],
        "requireNodeTypes": ["human_approval"],
    },
    {
        "id": "refund-wait-then-query",
        "prompt": "提交退款后等待10分钟再查询结果并通知客户。",
        "expectSkillAny": ["shop", "message", "database"],
        "expectToolAny": ["shop/list_pending_refunds", "database/query", "message/send"],
        "requireNodeTypes": ["wait"],
    },
]


def load_env_file(path: str) -> None:
    env_path = Path(path)
    if not env_path.is_file():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key and key not in os.environ:
            value = value.strip().strip("\"'")
            os.environ[key] = value


def _tools_of(graph: dict[str, Any]) -> list[str]:
    tools: list[str] = []
    for node in graph.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "tool_call":
            continue
        config = node.get("config")
        if isinstance(config, dict) and isinstance(config.get("tool"), str):
            tools.append(config["tool"])
    return tools


def _field_diff(expected: list[str], actual: list[str], field: str) -> dict[str, Any]:
    return {
        "field": field,
        "expectedAny": expected,
        "actual": actual,
        "hit": any(item in actual for item in expected),
        "missingAny": [] if any(item in actual for item in expected) else expected,
    }


def evaluate_case(case: dict[str, Any], registry: Any, deps: dict[str, Any]) -> dict[str, Any]:
    prompt = case["prompt"]
    skills = deps["recommend_skills"](prompt, registry)
    graph, generation = deps["generate_graph_with_meta"](prompt, registry=registry)
    compile_report = deps["validate_generated_graph"](graph, registry)
    node_types = [str(node.get("type")) for node in graph.get("nodes") or [] if isinstance(node, dict)]
    tools = _tools_of(graph)
    skill_ids = [str(skill.get("id")) for skill in skills]
    skill_diff = _field_diff(case["expectSkillAny"], skill_ids, "skills.top3")
    tool_diff = _field_diff(case["expectToolAny"], tools, "graph.tools")
    missing_node_types = [kind for kind in case.get("requireNodeTypes", []) if kind not in node_types]
    node_diff = {
        "field": "graph.nodeTypes",
        "required": case.get("requireNodeTypes", []),
        "actual": node_types,
        "missing": missing_node_types,
    }
    mode_ok = generation.get("mode") == "llm"
    compile_ok = bool(compile_report.get("ok"))
    case_ok = bool(skill_diff["hit"] and tool_diff["hit"] and not missing_node_types and compile_ok and mode_ok)
    result: dict[str, Any] = {
        "id": case["id"],
        "prompt": prompt,
        "ok": case_ok,
        "generation": generation,
        "top3": skills,
        "tools": tools,
        "nodeTypes": node_types,
        "compile": compile_report,
        "diffs": [skill_diff, tool_diff, node_diff],
        "checks": {
            "modeLlm": mode_ok,
            "compileOk": compile_ok,
            "skillHit": skill_diff["hit"],
            "toolHit": tool_diff["hit"],
            "nodeHit": not missing_node_types,
        },
    }
    if not case_ok:
        result["graph"] = graph
    return result


def _failure_result(case: dict[str, Any], error: Exception) -> dict[str, Any]:
    message = f"{type(error).__name__}: {error}"
    return {
        "id": case["id"],
        "prompt": case["prompt"],
        "ok": False,
        "generation": {"mode": "error"},
        "top3": [],
        "tools": [],
        "nodeTypes": [],
        "compile": {"ok": False, "errors": [message], "codes": ["LLM_CALL_ERROR"]},
        "diffs": [],
        "checks": {
            "modeLlm": False,
            "compileOk": False,
            "skillHit": False,
            "toolHit": False,
            "nodeHit": False,
        },
        "error": message,
    }


def _is_rate_limit(error: Exception) -> bool:
    text = str(error).lower()
    return "rate limit" in text or "rate_limit" in text or " 429" in text


def _summary(results: list[dict[str, Any]], elapsed: float) -> dict[str, Any]:
    failures = [result for result in results if not result.get("ok")]
    return {
        "total": len(results),
        "passed": len(results) - len(failures),
        "failed": len(failures),
        "llmMode": sum(1 for result in results if result.get("generation", {}).get("mode") == "llm"),
        "compileOk": sum(1 for result in results if result.get("checks", {}).get("compileOk")),
        "skillTop3Hit": sum(1 for result in results if result.get("checks", {}).get("skillHit")),
        "toolHit": sum(1 for result in results if result.get("checks", {}).get("toolHit")),
        "elapsedSeconds": round(elapsed, 2),
        "model": next(
            (
                result.get("generation", {}).get("model")
                for result in results
                if result.get("generation", {}).get("model")
            ),
            None,
        ),
    }


def _write_payload(
    path: Path,
    env_file: str,
    results: list[dict[str, Any]],
    elapsed: float,
) -> dict[str, Any]:
    failures = [result for result in results if not result.get("ok")]
    payload = {
        "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "envFile": env_file,
        "cases": results,
        "failures": failures,
        "summary": _summary(results, elapsed),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument("--max-cases", type=int, default=len(CASES))
    parser.add_argument("--delay", type=float, default=45.0, help="每条用例之间的间隔秒数")
    parser.add_argument("--retries", type=int, default=2, help="单条用例限流重试次数")
    parser.add_argument("--retry-wait", type=float, default=60.0)
    parser.add_argument("--resume", action="store_true", default=True)
    args = parser.parse_args()
    load_env_file(args.env_file)

    from atlas.api.main import _demo_registry
    from atlas.llm.nl_generate import generate_graph_with_meta, validate_generated_graph
    from atlas.skills.recommend import recommend_skills

    deps = {
        "recommend_skills": recommend_skills,
        "generate_graph_with_meta": generate_graph_with_meta,
        "validate_generated_graph": validate_generated_graph,
    }
    cases = CASES[: max(0, args.max_cases)]
    output = Path(args.output)
    existing: dict[str, dict[str, Any]] = {}
    if args.resume and output.is_file():
        try:
            existing_payload = json.loads(output.read_text(encoding="utf-8"))
            existing = {item["id"]: item for item in existing_payload.get("cases", [])}
        except (json.JSONDecodeError, KeyError, TypeError):
            existing = {}
    results: list[dict[str, Any]] = []
    started = time.time()
    rate_limit_streak = 0
    for index, case in enumerate(cases, start=1):
        old = existing.get(case["id"])
        if old and old.get("ok"):
            results.append(old)
            print(f"[SKIP] {case['id']}: 已通过，复用上次结果", flush=True)
            continue
        result: dict[str, Any]
        for attempt in range(1, args.retries + 2):
            try:
                result = evaluate_case(case, _demo_registry, deps)
                rate_limit_streak = 0
                break
            except Exception as exc:  # noqa: BLE001 - 评估脚本按用例归档错误
                if _is_rate_limit(exc):
                    rate_limit_streak += 1
                    if attempt <= args.retries:
                        wait = args.retry_wait * attempt
                        print(
                            f"[WAIT] {case['id']}: 限流，{wait:.0f}s 后重试 "
                            f"({attempt}/{args.retries})",
                            flush=True,
                        )
                        time.sleep(wait)
                        continue
                result = _failure_result(case, exc)
                rate_limit_streak = rate_limit_streak if _is_rate_limit(exc) else 0
                break
        results.append(result)
        print(
            f"[{'PASS' if result['ok'] else 'FAIL'}] {case['id']}: "
            f"mode={result['generation'].get('mode')} compile={result['checks']['compileOk']} "
            f"skills={','.join(s['id'] for s in result['top3'])}",
            flush=True,
        )
        payload = _write_payload(output, args.env_file, results, time.time() - started)
        if rate_limit_streak >= 2:
            print("[STOP] 连续限流，写入已完成用例后退出；稍后加 --resume 继续", flush=True)
            print(json.dumps(payload["summary"], ensure_ascii=False), flush=True)
            return 2
        if index < len(cases) and args.delay > 0:
            time.sleep(args.delay)
    payload = _write_payload(output, args.env_file, results, time.time() - started)
    print(json.dumps(payload["summary"], ensure_ascii=False), flush=True)
    return 0 if payload["summary"]["total"] == len(cases) and not payload["failures"] else 1


if __name__ == "__main__":
    sys.exit(main())
