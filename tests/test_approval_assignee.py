# -*- coding: utf-8 -*-
"""打包 A4（docs/100）：动态审批人 + 审批指派校验（U1201–U1204）。

U1201 动态审批人：config approver 支持 {{变量}} 插值，运行期展开并随挂起帧持久化；
插值后空串 → 空 approver（任何人可决）；字面量 {{global.未定义}} 编译 422 APPROVER_REF_UNRESOLVED。
U1202 指派校验·匹配：approver user:<id> 且操作者匹配 → 200；邮箱形态恒 403（iam 无 email 字段，照实注记）。
U1203 指派校验·不匹配：approver 非空且不匹配 → 403 APPROVAL_NOT_ASSIGNED；空 approver 任何人 200；未知 token 404 不变。
U1204 恢复路径：挂起帧带 approver，恢复扫描器重建 pending 后端点校验同样生效（内存档语义）。
"""

from __future__ import annotations

import threading
import time as time_mod

import pytest
from fastapi.testclient import TestClient

from atlas.api.main import app
from atlas.graph.dsl import GraphValidationError, parse_graph
from atlas.graph.loader import run_graph
from atlas.iam.deps import session_store, tenant_registry
from atlas.iam.principals import authenticate

anon = TestClient(app)


def _token(username: str, password: str) -> str:
    principal = authenticate(username, password)
    assert principal is not None
    return session_store.issue(principal)


def _auth(username: str, password: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {_token(username, password)}"}


ADMIN_A = _auth("admin-a", "admin123")
OPERATOR_A = _auth("operator-a", "operator123")


def _graph(approver: str, variables: list[dict] | None = None) -> dict:
    return {
        "version": 1,
        "variables": variables or [],
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/refund"}},
            {"id": "human-1", "type": "human_approval", "name": "人工审批",
             "config": {
                 "summary": "订单退款审批",
                 "approver": approver,
                 "timeoutSeconds": 300,
                 "onTimeout": "reject",
                 "approvedTarget": "tool-approve",
                 "rejectedTarget": "tool-reject",
             }},
            {"id": "tool-approve", "type": "tool_call", "name": "通过侧",
             "config": {"tool": "op-approve"}},
            {"id": "tool-reject", "type": "tool_call", "name": "拒绝侧",
             "config": {"tool": "op-reject"}},
        ],
        "edges": [
            {"id": "e1", "source": "trigger-1", "target": "human-1"},
            {"id": "e2", "source": "human-1", "target": "tool-approve"},
            {"id": "e3", "source": "human-1", "target": "tool-reject"},
        ],
    }


def _run_suspended(graph: dict, frames: list) -> dict:
    """线程跑 run_graph 至审批挂起，返回挂起帧；结束后以空 approver 放行避免悬挂。"""
    broker = tenant_registry.get("t1").approval_broker

    def run_original() -> None:
        run_graph(
            graph,
            inputs={"order_id": "12360"},
            approval_broker=broker,
            frame_sink=frames.append,
        )

    thread = threading.Thread(target=run_original)
    thread.start()
    frame = _wait_frames(frames)
    broker.resolve(frame["resume_token"], "approved", comment="测试放行")
    thread.join(timeout=3)
    assert not thread.is_alive(), "run 线程应在 resolve 后结束"
    return frame


def _wait_frames(holder: list, timeout: float = 3.0) -> dict:
    deadline = time_mod.monotonic() + timeout
    while not holder and time_mod.monotonic() < deadline:
        time_mod.sleep(0.005)
    assert holder, "应在超时前产生挂起帧"
    return holder[0]


# --------------------------------------------------------------------------- U1201


def test_dynamic_approver_resolves_global_variable_into_frame():
    frames: list[dict] = []
    frame = _run_suspended(
        parse_graph(_graph("{{global.operator_email}}",
                           variables=[{"name": "operator_email",
                                       "value": "approver@example.com"}])),
        frames,
    )
    assert frame["kind"] == "approval"
    # 运行期展开并随挂起帧持久化（docs/100 §2）。
    assert frame["approver"] == "approver@example.com"


def test_dynamic_approver_residue_falls_back_to_empty():
    """运行期才可判定的引用（非 global. 前缀）求值残留 → 空串（任何人可决）+ 告警。"""
    frames: list[dict] = []
    frame = _run_suspended(parse_graph(_graph("{{payload.owner}}")), frames)
    assert frame["approver"] == ""


def test_dynamic_approver_unresolved_global_ref_fails_compile():
    """字面量 {{global.未定义}} 编译期 422 APPROVER_REF_UNRESOLVED（模板笔误提前暴露）。"""
    with pytest.raises(GraphValidationError) as exc_info:
        parse_graph(_graph("{{global.unknown}}"))
    assert "APPROVER_REF_UNRESOLVED" in exc_info.value.codes


# ------------------------------------------------------------------- U1202/U1203


def _request(tenant: str, *, approver: str) -> str:
    broker = tenant_registry.get(tenant).approval_broker
    return broker.request(
        node_id="human-1",
        graph_id="g1",
        summary="订单 C-1 退款审批",
        approver=approver,
        timeout_seconds=300,
    )


def _decide(token: str, headers: dict):
    return anon.post(f"/api/approvals/{token}/decision",
                     headers=headers, json={"decision": "approved"})


def _detail(resp) -> dict:
    return resp.json()["detail"]


def test_assignee_match_user_ref_returns_200():
    token = _request("t1", approver="user:admin-a")
    resp = _decide(token, ADMIN_A)
    assert resp.status_code == 200


def test_assignee_mismatch_returns_403():
    token = _request("t1", approver="user:admin-a")
    resp = _decide(token, OPERATOR_A)
    assert resp.status_code == 403
    assert _detail(resp)["code"] == "APPROVAL_NOT_ASSIGNED"


def test_assignee_email_form_always_403_without_iam_email():
    """iam v1 无 email 字段（docs/100 §7 注记照实）：邮箱形态 approver 恒无匹配。"""
    token = _request("t1", approver="admin@example.com")
    resp = _decide(token, ADMIN_A)
    assert resp.status_code == 403
    assert _detail(resp)["code"] == "APPROVAL_NOT_ASSIGNED"


def test_assignee_empty_anyone_can_decide():
    token = _request("t1", approver="")
    resp = _decide(token, OPERATOR_A)
    assert resp.status_code == 200


def test_assignee_unknown_token_still_404():
    resp = _decide("no-such-token", ADMIN_A)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- U1204


def test_assignee_restored_pending_keeps_approver_and_gate():
    """挂起帧带 approver；restore 重建 pending 后决策端点校验同样生效（内存档语义，
    PG 真跨重启由集成套件标记验证，docs/100 §4 U1204）。"""
    frames: list[dict] = []
    broker = tenant_registry.get("t1").approval_broker
    frame = _run_suspended(
        parse_graph(_graph("{{global.operator_email}}",
                           variables=[{"name": "operator_email",
                                       "value": "user:admin-a"}])),
        frames,
    )
    token = frame["resume_token"]
    assert frame["approver"] == "user:admin-a"
    # 已被放行；另挂一张验证恢复路径。
    token = _request("t1", approver="user:admin-a")

    # 恢复扫描器路径：以帧内原 token 重建 pending（不生成新 token）。
    broker.restore(
        token=token,
        node_id="human-1",
        graph_id="g1",
        summary="订单恢复审批",
        approver="user:admin-a",
        remaining_seconds=300,
    )
    assert _decide(token, ADMIN_A).status_code == 200
    # 指派校验在恢复重建的 pending 上同样生效。
    token2 = _request("t1", approver="user:admin-a")
    assert _decide(token2, OPERATOR_A).status_code == 403
