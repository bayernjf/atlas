"""JSON-RPC 任务生命周期（docs/90）：内存台账 + plan/真实 run 双执行器。

形状与 loom backend/app/core/a2a/rpc.py（Q150，已与 Zeus 派发客户端真机对齐）
同构：send/sendSubscribe/get/cancel、submitted→working→终态事件、artifact 的
x-zeus-report。A-02 起新增 `run-graph`：调用方必须持有租户绑定 token，真实执行
一张非交互已保存图，并把结果写入租户 run_store。
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from atlas.a2a.auth import CallerIdentity
from atlas.a2a.run import A2ARunError, execute_graph_run
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
    "RUN_CONFLICT": (-32003, "Run conflict"),
    "IDEMPOTENCY_CONFLICT": (-32004, "Idempotency key conflict"),
    "TENANT_UNBOUND": (-32010, "Caller tenant unbound"),
    "TENANT_MISMATCH": (-32011, "Tenant mismatch"),
    "GRAPH_NOT_FOUND": (-32012, "Graph not found"),
    "INTERACTIVE_GRAPH_UNSUPPORTED": (-32013, "Interactive graph unsupported"),
    "GRAPH_INVALID": (-32014, "Graph invalid"),
}

MAX_TASKS = 500
TASK_TTL_SECONDS = 30 * 60

_tasks: dict[str, dict[str, Any]] = {}
_idempotency: dict[tuple[str, str], dict[str, str]] = {}
_active_runs: dict[tuple[str, str], str] = {}
_lock = threading.RLock()


class A2AProtocolError(Exception):
    def __init__(self, code_key: str, message: str, *, data: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code_key = code_key
        self.message = message
        self.data = data or {}


def _sweep(now: float) -> None:
    expired = [tid for tid, task in _tasks.items() if task["_expires_at"] <= now]
    for tid in expired:
        _tasks.pop(tid, None)
    for key, entry in list(_idempotency.items()):
        if entry["task_id"] not in _tasks:
            _idempotency.pop(key, None)
    while len(_tasks) > MAX_TASKS:
        _tasks.pop(next(iter(_tasks)), None)


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _error(code_key: str, detail: str | None = None) -> dict:
    code, message = _JSONRPC_ERRORS[code_key]
    return {"code": code, "message": detail or message}


def _error_with_data(code_key: str, detail: str | None, data: dict[str, Any]) -> dict:
    error = _error(code_key, detail)
    if data:
        error["data"] = data
    return error


def _snapshot(task: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in task.items() if not k.startswith("_")}


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


def _run_fingerprint(tenant_id: str, params: dict) -> str:
    body = {
        "tenant_id": tenant_id,
        "graph_id": params.get("graph_id"),
        "release_version": params.get("release_version"),
        "inputs": params.get("inputs") or {},
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _make_task(metadata: dict[str, Any] | None) -> dict[str, Any]:
    now = time.time()
    _sweep(now)
    task_id = str(uuid.uuid4())
    task: dict[str, Any] = {
        "kind": "task",
        "id": task_id,
        "contextId": str(uuid.uuid4()),
        "status": {"state": "submitted", "timestamp": _now_iso()},
        "artifacts": [],
        "metadata": metadata,
    }
    _tasks[task_id] = {**task, "_expires_at": now + TASK_TTL_SECONDS}
    return task


def _emit_run_failure(
    task: dict[str, Any],
    *,
    params: dict,
    tenant_id: str,
    graph_id: str,
    code: str,
    message: str,
    detail: dict[str, Any] | None = None,
) -> None:
    task["artifacts"] = [
        {
            "artifactId": str(uuid.uuid4()),
            "name": "run-graph-failed",
            "parts": [
                {
                    "kind": "data",
                    "data": {
                        "mode": "run",
                        "skill": "run-graph",
                        "params": params,
                        "tenantId": tenant_id,
                        "graphId": graph_id,
                        "status": "failed",
                        "error": message,
                        "errorCode": code,
                        **(detail or {}),
                    },
                },
                {"kind": "text", "text": message},
            ],
            "x-zeus-report": {
                "summary": f"run-graph rejected for tenant={tenant_id}, graph={graph_id}: {message}",
                "evidence": [f"real-run rejected before execution: {code}"],
                "cost": {"llmTokens": 0, "wallSeconds": 0.0},
                "followUps": [],
            },
        }
    ]
    _tasks[task["id"]]["artifacts"] = task["artifacts"]
    task["status"] = {"state": "failed", "timestamp": _now_iso()}


def _emit_run_artifact(
    task: dict[str, Any],
    *,
    skill: str,
    params: dict,
    tenant_id: str,
    result: Any,
    caller_id: str,
) -> dict[str, Any]:
    artifact = {
        "artifactId": str(uuid.uuid4()),
        "name": f"{skill}-run",
        "parts": [
            {
                "kind": "data",
                "data": {
                    "mode": "run",
                    "skill": skill,
                    "params": params,
                    "tenantId": tenant_id,
                    "graphId": result.graph_id,
                    "runId": result.run_id,
                    "status": result.status,
                    "outputs": result.outputs,
                    "trace": result.trace,
                    "error": result.error,
                },
            },
            {
                "kind": "text",
                "text": (
                    f"A2A real run {result.run_id} on graph {result.graph_id}: "
                    f"status={result.status}, nodes={len(result.trace)}."
                ),
            },
        ],
        "x-zeus-report": {
            "summary": (
                f"run-graph completed for tenant={tenant_id}, graph={result.graph_id}, "
                f"caller={caller_id}, run={result.run_id}."
                if result.status == "completed"
                else f"run-graph ended with status={result.status} for tenant={tenant_id}, "
                f"graph={result.graph_id}, caller={caller_id}, run={result.run_id}."
            ),
            "evidence": [
                f"real run: mode=run (not plan-only), caller={caller_id}",
                f"tenant-bound token: caller tenant={tenant_id}",
                f"graph executed in-process: graph_id={result.graph_id}, run_id={result.run_id}",
                f"nodes executed={len(result.trace)}; trace included in artifact data",
                f"run_store recorded: run_id={result.run_id}",
            ],
            "cost": {
                "llmTokens": 0,
                "wallSeconds": result.wall_seconds,
                "tokenAccounting": "not-metered",
            },
            "followUps": [],
        },
    }
    if result.error:
        artifact["x-zeus-report"]["evidence"].append(f"error: {result.error}")
    task["artifacts"] = [artifact]
    return artifact


def _execute_plan_task(
    *,
    task: dict[str, Any],
    skill: str,
    params: dict,
    run_id: str | None,
    emit: Callable[[dict], None],
    status_event: Callable[[str, bool], dict],
) -> None:
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
        _tasks[task["id"]]["artifacts"] = [artifact]
        artifact_event = {
            "kind": "artifact-update",
            "taskId": task["id"],
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


def _try_replay_run_task(params: dict, caller: CallerIdentity | None) -> dict[str, Any] | None:
    """同 tenant+idempotency_key 的已完成请求直接返回原 task，不新建任务/不重跑。"""
    tenant_id = params.get("tenant_id")
    graph_id = params.get("graph_id")
    idempotency_key = params.get("idempotency_key")
    if not isinstance(tenant_id, str) or not isinstance(graph_id, str):
        return None
    if not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128:
        return None
    if caller is None or caller.tenant_id is None:
        return None
    if caller.tenant_id != tenant_id:
        return None
    fingerprint = _run_fingerprint(tenant_id, params)
    with _lock:
        _sweep(time.time())
        entry = _idempotency.get((tenant_id, idempotency_key))
        if entry is None:
            return None
        existing = _tasks.get(entry["task_id"])
        if existing is None:
            _idempotency.pop((tenant_id, idempotency_key), None)
            return None
        if entry["fingerprint"] != fingerprint:
            raise A2AProtocolError(
                "IDEMPOTENCY_CONFLICT",
                "idempotency_key was already used for a different run request",
                data={"taskId": entry["task_id"]},
            )
        return _snapshot(existing)


def _execute_run_task(
    *,
    task: dict[str, Any],
    skill: str,
    params: dict,
    caller: CallerIdentity | None,
    run_id: str | None,
    emit: Callable[[dict], None],
    status_event: Callable[[str, bool], dict],
) -> None:
    tenant_id = params.get("tenant_id")
    graph_id = params.get("graph_id")
    missing = [name for name, value in (("tenant_id", tenant_id), ("graph_id", graph_id)) if not value]
    if missing:
        task["status"] = {"state": "input-required", "timestamp": _now_iso()}
        emit(status_event("input-required", True))
        return
    if not isinstance(tenant_id, str) or not isinstance(graph_id, str):
        raise A2AProtocolError("INVALID_PARAMS", "tenant_id and graph_id must be strings")
    if caller is None or caller.tenant_id is None:
        raise A2AProtocolError(
            "TENANT_UNBOUND",
            (
                "run-graph requires a tenant-bound A2A token. Configure "
                "ATLAS_A2A_TASK_TOKENS=caller:tenant:token,...; legacy single-token "
                "callers are plan-only."
            ),
            data={"callerId": caller.caller_id if caller else None},
        )
    if caller.tenant_id != tenant_id:
        raise A2AProtocolError(
            "TENANT_MISMATCH",
            f"caller {caller.caller_id} is bound to tenant {caller.tenant_id}, not {tenant_id}",
            data={"callerId": caller.caller_id, "callerTenantId": caller.tenant_id, "requestedTenantId": tenant_id},
        )

    inputs = params.get("inputs")
    if inputs is not None and not isinstance(inputs, dict):
        raise A2AProtocolError("INVALID_PARAMS", "inputs must be an object")
    release_version = params.get("release_version")
    if release_version is not None and (not isinstance(release_version, int) or release_version < 1):
        raise A2AProtocolError("INVALID_PARAMS", "release_version must be a positive integer")
    idempotency_key = params.get("idempotency_key")
    if idempotency_key is not None and (
        not isinstance(idempotency_key, str) or not idempotency_key.strip() or len(idempotency_key) > 128
    ):
        raise A2AProtocolError("INVALID_PARAMS", "idempotency_key must be a non-empty string <=128 chars")

    fingerprint = _run_fingerprint(tenant_id, params)
    idem_slot = (tenant_id, idempotency_key) if isinstance(idempotency_key, str) else None
    if idem_slot is not None:
        with _lock:
            entry = _idempotency.get(idem_slot)
            if entry is not None:
                existing = _tasks.get(entry["task_id"])
                if existing is not None:
                    if entry["fingerprint"] != fingerprint:
                        raise A2AProtocolError(
                            "IDEMPOTENCY_CONFLICT",
                            "idempotency_key was already used for a different run request",
                            data={"taskId": entry["task_id"]},
                        )
                    task.update({
                        "status": existing["status"],
                        "artifacts": existing["artifacts"],
                        "metadata": existing.get("metadata"),
                    })
                    _tasks[task["id"]] = {**existing, "id": task["id"]}
                    return
                _idempotency.pop(idem_slot, None)

    run_id = f"a2a-{uuid.uuid4().hex}"
    if isinstance(task.get("metadata"), dict):
        task["metadata"]["a2a"]["runId"] = run_id
        _tasks[task["id"]]["metadata"] = task["metadata"]

    run_lock = (tenant_id, graph_id)
    with _lock:
        active_task = _active_runs.get(run_lock)
        if active_task is not None:
            active = _tasks.get(active_task)
            raise A2AProtocolError(
                "RUN_CONFLICT",
                f"a run for tenant={tenant_id} graph={graph_id} is already active",
                data={
                    "activeTaskId": active_task,
                    "activeRunId": (active or {}).get("metadata", {}).get("a2a", {}).get("runId"),
                },
            )
        _active_runs[run_lock] = task["id"]
        if idem_slot is not None:
            _idempotency[idem_slot] = {"task_id": task["id"], "fingerprint": fingerprint}

    result = None
    try:
        result = execute_graph_run(
            tenant_id=tenant_id,
            graph_id=graph_id,
            inputs=inputs,
            release_version=release_version,
            run_id=run_id,
        )
    except A2ARunError as exc:
        _emit_run_failure(task, params=params, tenant_id=tenant_id, graph_id=graph_id, code=exc.code, message=exc.message, detail=exc.data)
        emit(status_event("failed", True))
    except Exception as exc:  # noqa: BLE001 - 执行体异常必须变成可见 failed task
        message = f"{type(exc).__name__}: {exc}"[:500]
        _emit_run_failure(task, params=params, tenant_id=tenant_id, graph_id=graph_id, code="INTERNAL", message=message)
        emit(status_event("failed", True))
    finally:
        with _lock:
            if _active_runs.get(run_lock) == task["id"]:
                _active_runs.pop(run_lock, None)

    if result is None:
        return

    if isinstance(task.get("metadata"), dict):
        a2a_meta = task["metadata"].setdefault("a2a", {})
        a2a_meta["runId"] = result.run_id
        _tasks[task["id"]]["metadata"] = task["metadata"]

    artifact = _emit_run_artifact(
        task,
        skill=skill,
        params=params,
        tenant_id=tenant_id,
        result=result,
        caller_id=caller.caller_id,
    )
    _tasks[task["id"]]["artifacts"] = [artifact]
    artifact_event = {
        "kind": "artifact-update",
        "taskId": task["id"],
        "contextId": task["contextId"],
        "artifact": artifact,
    }
    if run_id:
        artifact_event["x-zeus"] = {"runId": run_id}
    emit(artifact_event)
    state = "completed" if result.status == "completed" else (
        "input-required" if result.status == "input-required" else "failed"
    )
    task["status"] = {"state": state, "timestamp": _now_iso()}
    emit(status_event(state, True))


def execute_task(
    message: dict,
    on_event: Callable[[dict], None] | None = None,
    *,
    caller: CallerIdentity | None = None,
) -> dict:
    """受理 → working → 终态，返回最终 task dict；事件经 on_event 逐个回调。"""
    skill, params = _parse_skill_and_params(message)
    if skill == "run-graph":
        replay = _try_replay_run_task(params, caller)
        if replay is not None:
            return replay
    incoming_run_id = (message.get("metadata") or {}).get("x-zeus-runId")
    metadata: dict[str, Any] | None = (
        {"x-zeus-runId": incoming_run_id} if incoming_run_id else None
    )
    if skill == "run-graph" and caller is not None:
        metadata = {
            **(metadata or {}),
            "a2a": {"callerId": caller.caller_id, "tenantId": caller.tenant_id},
        }

    with _lock:
        task = _make_task(metadata)

    def emit(event: dict) -> None:
        if on_event is not None:
            on_event(event)

    def status_event(state: str, final: bool) -> dict:
        event = {
            "kind": "status-update",
            "taskId": task["id"],
            "contextId": task["contextId"],
            "status": {"state": state, "timestamp": _now_iso()},
            "final": final,
        }
        if incoming_run_id:
            event["x-zeus"] = {"runId": incoming_run_id}
        return event

    emit(status_event("submitted", False))
    task["status"] = {"state": "working", "timestamp": _now_iso()}
    _tasks[task["id"]]["status"] = task["status"]
    emit(status_event("working", False))

    try:
        if skill == "run-graph":
            _execute_run_task(
                task=task,
                skill=skill,
                params=params,
                caller=caller,
                run_id=incoming_run_id,
                emit=emit,
                status_event=status_event,
            )
        else:
            _execute_plan_task(
                task=task,
                skill=skill,
                params=params,
                run_id=incoming_run_id,
                emit=emit,
                status_event=status_event,
            )
    except A2AProtocolError:
        with _lock:
            _tasks.pop(task["id"], None)
            for key, entry in list(_idempotency.items()):
                if entry["task_id"] == task["id"]:
                    _idempotency.pop(key, None)
        raise

    with _lock:
        _tasks[task["id"]]["status"] = task["status"]
        _tasks[task["id"]]["artifacts"] = task["artifacts"]
        _tasks[task["id"]]["_expires_at"] = time.time() + TASK_TTL_SECONDS
        return _snapshot(_tasks[task["id"]])


def handle_jsonrpc(
    payload: dict,
    on_event: Callable[[dict], None] | None = None,
    *,
    caller: CallerIdentity | None = None,
) -> dict:
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
            task = execute_task(message, on_event, caller=caller)
        except ValueError as exc:
            return {"jsonrpc": "2.0", "id": request_id, "error": _error("INVALID_PARAMS", str(exc))}
        except A2AProtocolError as exc:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": _error_with_data(exc.code_key, exc.message, exc.data),
            }
        return {"jsonrpc": "2.0", "id": request_id, "result": task}

    if method == "tasks/get":
        task_id = params.get("id")
        with _lock:
            task = _tasks.get(task_id) if isinstance(task_id, str) else None
        if task is None:
            return {"jsonrpc": "2.0", "id": request_id, "error": _error("TASK_NOT_FOUND")}
        return {"jsonrpc": "2.0", "id": request_id, "result": _snapshot(task)}

    if method == "tasks/cancel":
        task_id = params.get("id")
        with _lock:
            task = _tasks.get(task_id) if isinstance(task_id, str) else None
        if task is None:
            return {"jsonrpc": "2.0", "id": request_id, "error": _error("TASK_NOT_FOUND")}
        if task["status"]["state"] in TERMINAL_STATES:
            return {"jsonrpc": "2.0", "id": request_id, "error": _error("TASK_NOT_CANCELABLE")}
        with _lock:
            task["status"] = {"state": "canceled", "timestamp": _now_iso()}
        # A2A v1 只标记任务态，不向真实 run 发取消信号；run-graph 在提交时同步完成，
        # 不存在可取消的中间窗口。
        return {"jsonrpc": "2.0", "id": request_id, "result": _snapshot(task)}

    return {"jsonrpc": "2.0", "id": request_id, "error": _error("METHOD_NOT_FOUND")}


def reset_for_tests() -> None:
    with _lock:
        _tasks.clear()
        _idempotency.clear()
        _active_runs.clear()
