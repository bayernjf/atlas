"""JSON-RPC 任务生命周期（docs/90）：纯内存、纯函数式，供 router 调用与单测覆盖。

形状与 loom backend/app/core/a2a/rpc.py（Q150，已与 Zeus 派发客户端真机对齐）
逐字节同构：send/sendSubscribe/get/cancel、submitted→working→终态事件、
artifact 的 x-zeus-report。差异仅在 plan 执行器（atlas skills）。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from typing import Any

from atlas.a2a.skills import run_plan_skill

TERMINAL_STATES = ("completed", "failed", "canceled")

_JSONRPC_ERRORS = {
    "PARSE": (-32700, "Parse error"),
    "INVALID_REQUEST": (-32600, "Invalid Request"),
    "METHOD_NOT_FOUND": (-32601, "Method not found"),
    "INVALID_PARAMS": (-32602, "Invalid params"),
    "INTERNAL": (-32603, "Internal error"),
    "TASK_NOT_FOUND": (-32001, "Task not found"),
    "TASK_NOT_CANCELABLE": (-32002, "Task not cancelable"),
}

MAX_TASKS = 500
TASK_TTL_SECONDS = 30 * 60

_tasks: dict[str, dict[str, Any]] = {}


def _sweep(now: float) -> None:
    expired = [tid for tid, task in _tasks.items() if task["_expires_at"] <= now]
    for tid in expired:
        _tasks.pop(tid, None)
    while len(_tasks) > MAX_TASKS:
        _tasks.pop(next(iter(_tasks)), None)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _error(code_key: str, detail: str | None = None) -> dict:
    code, message = _JSONRPC_ERRORS[code_key]
    return {"code": code, "message": detail or message}


def _is_valid_request(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("jsonrpc") == "2.0"
        and isinstance(value.get("method"), str)
    )


def _parse_skill_and_params(message: dict) -> tuple[str, dict]:
    parts = message.get("parts")
    data: dict | None = None
    if isinstance(parts, list):
        for part in parts:
            if isinstance(part, dict) and part.get("kind") == "data" and isinstance(part.get("data"), dict):
                data = part["data"]
                break
    if data is not None:
        skill = data.get("skill")
        params = {k: v for k, v in data.items() if k != "skill"}
    else:
        # 标准 A2A 超集兜底（docs/90 §3，对齐 pr-helper parseSkillAndParams）：
        # 只懂标准协议的调用方发纯 text part（不带 Zeus 的 data part）时，取第一个
        # 非空 text 的首个空白分词作 skill、参数为空；缺必填参数由 skill 层回
        # input-required（合法标准终态），而不是在协议层 -32602 拒绝标准调用方。
        skill = None
        params = {}
        if isinstance(parts, list):
            for part in parts:
                if isinstance(part, dict) and part.get("kind") == "text" and isinstance(part.get("text"), str):
                    token = part["text"].strip().split()
                    if token:
                        skill = token[0]
                        break
    if isinstance(skill, str) and skill:
        return skill, params
    raise ValueError("no skill found in message; include a data part with a skill field or a text part")


def execute_task(message: dict, on_event: Callable[[dict], None] | None = None) -> dict:
    """受理 → working → 终态，返回最终 task dict；事件经 on_event 逐个回调。"""
    skill, params = _parse_skill_and_params(message)
    run_id = (message.get("metadata") or {}).get("x-zeus-runId")
    now = time.time()
    _sweep(now)
    task_id = str(uuid.uuid4())
    task: dict[str, Any] = {
        "kind": "task",
        "id": task_id,
        "contextId": str(uuid.uuid4()),
        "status": {"state": "submitted", "timestamp": _now_iso()},
        "artifacts": [],
        "metadata": {"x-zeus-runId": run_id} if run_id else None,
    }
    _tasks[task_id] = {**task, "_expires_at": now + TASK_TTL_SECONDS}

    def emit(event: dict) -> None:
        if on_event is not None:
            on_event(event)

    def status_event(state: str, final: bool) -> dict:
        event = {
            "kind": "status-update",
            "taskId": task_id,
            "contextId": task["contextId"],
            "status": {"state": state, "timestamp": _now_iso()},
            "final": final,
        }
        if run_id:
            event["x-zeus"] = {"runId": run_id}
        return event

    emit(status_event("submitted", False))
    task["status"] = {"state": "working", "timestamp": _now_iso()}
    _tasks[task_id]["status"] = task["status"]
    emit(status_event("working", False))

    started = time.time()
    result = run_plan_skill(skill, params)
    wall_seconds = round(time.time() - started, 3)

    if result["state"] == "completed":
        artifact = {
            "artifactId": str(uuid.uuid4()),
            "name": result["name"],
            "parts": [
                {"kind": "data", "data": {"mode": "plan", "skill": skill, "params": params}},
                {"kind": "text", "text": "\n".join(f"{i + 1}. {step}" for i, step in enumerate(result["steps"]))},
            ],
            "x-zeus-report": {
                "summary": result["summary"],
                "evidence": [
                    f"parameters validated: {', '.join(sorted(params))}",
                    "plan-only: no graph mutation, no run started, no token spend, no approval resolved",
                ],
                "cost": {"llmTokens": 0, "wallSeconds": wall_seconds},
                "followUps": [],
            },
        }
        task["artifacts"] = [artifact]
        _tasks[task_id]["artifacts"] = [artifact]
        artifact_event = {
            "kind": "artifact-update",
            "taskId": task_id,
            "contextId": task["contextId"],
            "artifact": artifact,
        }
        if run_id:
            artifact_event["x-zeus"] = {"runId": run_id}
        emit(artifact_event)
        task["status"] = {"state": "completed", "timestamp": _now_iso()}
        emit(status_event("completed", True))
    elif result["state"] == "input-required":
        task["status"] = {"state": "input-required", "timestamp": _now_iso()}
        emit(status_event("input-required", True))
    else:
        task["status"] = {"state": "failed", "timestamp": _now_iso()}
        emit(status_event("failed", True))

    _tasks[task_id]["status"] = task["status"]
    _tasks[task_id].pop("_expires_at", None)
    _tasks[task_id]["_expires_at"] = time.time() + TASK_TTL_SECONDS
    return task


def handle_jsonrpc(payload: dict, on_event: Callable[[dict], None] | None = None) -> dict:
    """分发单个 JSON-RPC 请求，返回响应 dict（SSE 与同步共用）。"""
    request_id = payload.get("id")
    if not _is_valid_request(payload):
        return {"jsonrpc": "2.0", "id": request_id, "error": _error("INVALID_REQUEST")}
    method = payload.get("method")
    params = payload.get("params") or {}

    if method in ("tasks/send", "tasks/sendSubscribe"):
        message = params.get("message")
        if not isinstance(message, dict) or message.get("role") != "user" or not isinstance(message.get("parts"), list):
            return {"jsonrpc": "2.0", "id": request_id, "error": _error("INVALID_PARAMS", "params.message must be a user message with parts")}
        try:
            task = execute_task(message, on_event)
        except ValueError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": _error("INVALID_PARAMS", str(exc))}
        return {"jsonrpc": "2.0", "id": request_id, "result": task}

    if method == "tasks/get":
        task_id = params.get("id")
        task = _tasks.get(task_id) if isinstance(task_id, str) else None
        if task is None:
            return {"jsonrpc": "2.0", "id": request_id, "error": _error("TASK_NOT_FOUND")}
        return {"jsonrpc": "2.0", "id": request_id, "result": {k: v for k, v in task.items() if not k.startswith("_")}}

    if method == "tasks/cancel":
        task_id = params.get("id")
        task = _tasks.get(task_id) if isinstance(task_id, str) else None
        if task is None:
            return {"jsonrpc": "2.0", "id": request_id, "error": _error("TASK_NOT_FOUND")}
        if task["status"]["state"] in TERMINAL_STATES:
            return {"jsonrpc": "2.0", "id": request_id, "error": _error("TASK_NOT_CANCELABLE")}
        task["status"] = {"state": "canceled", "timestamp": _now_iso()}
        return {"jsonrpc": "2.0", "id": request_id, "result": {k: v for k, v in task.items() if not k.startswith("_")}}

    return {"jsonrpc": "2.0", "id": request_id, "error": _error("METHOD_NOT_FOUND")}


def reset_for_tests() -> None:
    _tasks.clear()
