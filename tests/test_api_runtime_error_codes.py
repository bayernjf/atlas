"""运行期失败结构化错误码契约测试（docs/60 G1）。

- 流式运行 run failed 经 SSE `event: error` 帧下发 `{detail:{code,message,params}}`；
- ConditionEvalError 经全局 handler 返回结构化 500（与 WaitNodeFailure 同形）；
- runtime_error_meta 把 wait/condition/未预期异常归一化为 errorCode/errorParams。
中文 message/旧 error 字符串始终保留为兜底。
"""

from __future__ import annotations

import json

from atlas.graph.conditions import ConditionEvalError
from atlas.graph.loader import WaitNodeFailure, runtime_error_meta

from tests.test_api_graphs import client


def _wait_fail_graph(timeout_seconds: int = 1):
    return {
        "version": 1,
        "variables": [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "t",
             "config": {"triggerType": "manual"}},
            {"id": "wait-1", "type": "wait", "name": "等待",
             "config": {"waitType": "event", "eventKey": "u600_never_fires",
                        "timeoutSeconds": timeout_seconds, "onTimeout": "fail"}},
            {"id": "tool-after", "type": "tool_call", "name": "后继",
             "config": {"tool": "web-playwright/click"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "wait-1"},
            {"id": "e2", "source": "wait-1", "target": "tool-after"},
        ],
    }


def test_stream_run_failure_emits_structured_sse_error_frame():
    client.post("/api/demo/reset")
    gid = client.post("/api/graphs", json=_wait_fail_graph()).json()["id"]
    with client.stream("POST", f"/api/graphs/{gid}/run/stream", json={}) as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        lines = list(response.iter_lines())

    # 找到 error 帧及其 data 行
    error_idx = next(i for i, line in enumerate(lines) if line == "event: error")
    data_line = next(line for line in lines[error_idx + 1:] if line.startswith("data: "))
    detail = json.loads(data_line[6:])["detail"]
    assert detail["code"] == "WAIT_TIMEOUT_FAILED"
    assert detail["params"]["nodeId"] == "wait-1"
    assert detail["message"]  # 中文兜底 message 保留
    # docs/101 D58：已知业务异常 message 保留中文业务文案、不带类型名前缀。
    assert not detail["message"].startswith("WaitNodeFailure:")
    # 终帧不是 result（运行失败）
    assert "event: result" not in lines


def test_condition_eval_error_handler_returns_structured_500():
    from atlas.api.main import condition_eval_failure_handler

    exc = ConditionEvalError(
        '算术 "/" 除数不能为 0',
        code="COND_DIVIDE_BY_ZERO",
        params={"op": "/"},
    )
    response = condition_eval_failure_handler(None, exc)
    assert response.status_code == 500
    detail = json.loads(response.body)["detail"]
    assert detail["code"] == "COND_DIVIDE_BY_ZERO"
    assert detail["params"] == {"op": "/"}
    assert "除数" in detail["message"]


def test_runtime_error_meta_normalizes_known_and_unexpected():
    wait = runtime_error_meta(WaitNodeFailure("node-x", "WAIT_DURATION_INVALID", "坏值"))
    assert wait == {"errorCode": "WAIT_DURATION_INVALID",
                    "errorParams": {"nodeId": "node-x"}}

    cond = runtime_error_meta(
        ConditionEvalError("类型不符", code="COND_TYPE_MISMATCH",
                           params={"op": "+", "expected": "number", "actual": "string"})
    )
    assert cond["errorCode"] == "COND_TYPE_MISMATCH"
    assert cond["errorParams"]["actual"] == "string"

    fallback = runtime_error_meta(RuntimeError("boom"))
    assert fallback == {"errorCode": "RUNTIME_UNEXPECTED", "errorParams": {}}


# ------------------------------------------------------------------ docs/101 D59


def test_health_reports_demo_surface_capability():
    """/api/health 带 demo_surface 能力位（前端据此决定是否暴露演示专用入口）。

    非 prod 档恒开；prod 档默认关（fail-closed）、ATLAS_ENABLE_DEMO_MOCK=1 才开。
    """
    from atlas.security.bootstrap import demo_surface_enabled

    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["demo_surface"] is demo_surface_enabled()
    assert isinstance(body["demo_surface"], bool)
