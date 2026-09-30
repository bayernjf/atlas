# -*- coding: utf-8 -*-
"""prod 档 ai_decision 禁静默 mock 兜底（docs/73 §1.1 W1-1.1 后半，U1006–U1010）。

改前实况：prod 没配 `LITELLM_MODEL` 时 `get_decision_client()` 返回
`RuleBasedDecisionClient`，`ai_decision` 节点照常"执行成功"——图上写着"AI 决策"，实际按
写死的退款规则判。这是最坏的一类降级：不报错、不告警，下游真实副作用照着假结论执行。

本门与 R8（`_execute_tool` 的 prod 演示适配器门）同形：**进程照常启动**（prod 的非 LLM
部署不该被拦），只在节点真要执行且决策器是规则兜底时把该 run 显式标 failed。唯一的开闸
方式是 `ATLAS_ENABLE_DEMO_MOCK=1`（演示实例）。
"""

from __future__ import annotations

import json

import pytest
from fastapi.responses import JSONResponse

from atlas.api.main import ai_decision_unavailable_handler
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import (
    AiDecisionUnavailable,
    run_graph,
    runtime_error_meta,
)
from atlas.llm.decision import HUMAN_APPROVAL, RuleBasedDecisionClient


def _decision_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [
                {"name": "limit", "type": "number", "value": "100", "scope": "global"}
            ],
            "nodes": [
                {
                    "id": "ai-1",
                    "type": "ai_decision",
                    "name": "决策",
                    "config": {"promptTemplate": "运营提示词 {{global.limit}}"},
                }
            ],
            "edges": [],
        }
    )


class _FakeLlmClient:
    """非规则决策器（冒充 LiteLLM 客户端）：本门只认 RuleBasedDecisionClient。

    置信度给 0.9（≥ 缺省阈值 0.6），避免落到挂起转人工分支去等审批。
    """

    def decide_refund(self, *, reason, amount, limit, prompt="", model=None):
        return {
            "action": HUMAN_APPROVAL,
            "reason": "fake llm",
            "confidence": 0.9,
            "source": "llm:fake",
        }


@pytest.fixture(autouse=True)
def _neutral_profile():
    """每条用例自带档位，不继承机器上残留的 ATLAS_ENV。"""
    import os

    saved = os.environ.get("ATLAS_ENV")
    os.environ["ATLAS_ENV"] = "dev"
    os.environ.pop("ATLAS_ENABLE_DEMO_MOCK", None)
    yield
    if saved is None:
        os.environ.pop("ATLAS_ENV", None)
    else:
        os.environ["ATLAS_ENV"] = saved
    os.environ.pop("ATLAS_ENABLE_DEMO_MOCK", None)


# --- U1006 prod 无 LLM：显式 failed，不静默走规则 ---------------------------


def test_u1006_prod_rule_fallback_is_refused_with_a_machine_code(monkeypatch):
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_ENABLE_DEMO_MOCK", raising=False)
    with pytest.raises(AiDecisionUnavailable) as excinfo:
        run_graph(_decision_graph(), decision_client=RuleBasedDecisionClient())
    assert excinfo.value.code == "LLM_DECISION_UNAVAILABLE"
    assert excinfo.value.node_id == "ai-1"
    message = str(excinfo.value)
    # 排障要看到"该配什么"与"怎么开闸"，否则线上只会看到一句"失败了"。
    for token in ("LITELLM_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
                  "ATLAS_ENABLE_DEMO_MOCK=1"):
        assert token in message, f"文案缺少 {token}：{message}"


def test_u1007_prod_with_llm_client_runs_unchanged(monkeypatch):
    """门只认规则兜底：真接了模型（任何非 RuleBasedDecisionClient）照常执行。"""
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_ENABLE_DEMO_MOCK", raising=False)
    result = run_graph(_decision_graph(), decision_client=_FakeLlmClient())
    assert result["status"] == "completed"
    assert result["outputs"]["ai-1"]["decision"]["source"] == "llm:fake"


def test_u1008_demo_mock_flag_is_the_only_escape_hatch(monkeypatch):
    """prod + 显式 ATLAS_ENABLE_DEMO_MOCK=1：演示实例恢复规则兜底（唯一开闸方式）。"""
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_ENABLE_DEMO_MOCK", "1")
    result = run_graph(_decision_graph(), decision_client=RuleBasedDecisionClient())
    assert result["status"] == "completed"
    assert result["outputs"]["ai-1"]["decision"]["source"] == "rule"


def test_u1009_dev_profile_is_untouched(monkeypatch):
    """非 prod 零变化：本地/演示不配模型时规则兜底照旧（本门不误伤开发流程）。"""
    monkeypatch.setenv("ATLAS_ENV", "dev")
    monkeypatch.delenv("ATLAS_ENABLE_DEMO_MOCK", raising=False)
    result = run_graph(_decision_graph(), decision_client=RuleBasedDecisionClient())
    assert result["status"] == "completed"
    assert result["outputs"]["ai-1"]["decision"]["source"] == "rule"


def test_u1010_run_failure_carries_code_and_node_id_to_the_client(monkeypatch):
    """终态归一化 + HTTP 形状：前端按码渲染双语文案，中文 message 仍是兜底真相。"""
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_ENABLE_DEMO_MOCK", raising=False)
    exc = AiDecisionUnavailable("ai-1", "生产环境未配置 LLM 决策器")
    assert runtime_error_meta(exc) == {
        "errorCode": "LLM_DECISION_UNAVAILABLE",
        "errorParams": {"nodeId": "ai-1"},
    }
    response = ai_decision_unavailable_handler(None, exc)
    assert isinstance(response, JSONResponse) and response.status_code == 500
    detail = json.loads(bytes(response.body))
    assert detail["detail"]["code"] == "LLM_DECISION_UNAVAILABLE"
    assert detail["detail"]["nodeId"] == "ai-1"
    assert "LLM_DECISION_UNAVAILABLE" in detail["detail"]["message"]
