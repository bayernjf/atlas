# -*- coding: utf-8 -*-
"""prod 档 condition(llm) 禁静默走默认分支（docs/73 W5-5.4，U1011–U1016）。

改前实况：prod 没配 `LITELLM_MODEL` 时 `get_condition_classifier()` 返回
`OfflineConditionClassifier`，它的 `classify()` 恒抛 `ConditionClassifyError`，loader 把 label
落成 `__default__` 走 `config["defaultTarget"]`——节点不报错、run 仍 `completed`，只在
`llm_errors` 里留一行「LLM 未配置，语义分支无法求值」。与 `ai_decision` 的规则兜底同族：
prod 档静默降级 ＋ 运行状态报绿；图内若没有 `ai_decision` 节点，这条路径没有任何门兜住。

本门与 `ai_decision` 的 prod 门（docs/73 W1-1.1）同形：**进程照常启动**，只在节点真执行且
分类器是离线档时把该 run 显式标 failed。唯一开闸方式是 `ATLAS_ENABLE_DEMO_MOCK=1`。

**判据是分类器类型，不是异常类型**：回放的 `ScriptedConditionClassifier`（docs/83 打包 V）与
`LiteLLMConditionClassifier` 的真调用失败（网络/配额/坏 JSON/标签越界）都保持原 fail-safe——
「接了模型但这次调用失败」与「根本没接模型」不是一回事。
"""

from __future__ import annotations

import json

import pytest
from fastapi.responses import JSONResponse

from atlas.api.main import condition_classifier_unavailable_handler
from atlas.graph.dsl import parse_graph
from atlas.graph.loader import (
    ConditionClassifierUnavailable,
    run_graph,
    runtime_error_meta,
)
from atlas.llm.condition_classifier import (
    OfflineConditionClassifier,
    ScriptedConditionClassifier,
)


def _llm_condition_graph():
    """trigger → condition(llm) → 三个 message 汇聚点（分支 target 须互异）。

    汇聚点用**全限定** `message/send`（而非裸工具名）＋静态 JSON params：prod 档下既过 R8
    （裸名会显式 FAILED）又在本进程内确定性成功，使断言只反映 condition 路由本身。
    """
    def _sink(node_id: str, name: str, subject: str) -> dict:
        return {
            "id": node_id,
            "type": "tool_call",
            "name": name,
            "config": {
                "tool": "message/send",
                "params": json.dumps(
                    {
                        "channel": "email",
                        "to": ["ops@example.com"],
                        "subject": subject,
                        "body": "分流",
                    }
                ),
            },
        }

    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "t",
                 "config": {"triggerType": "manual"}},
                {"id": "cond-1", "type": "condition", "name": "分流",
                 "position": {"x": 2, "y": 0},
                 "config": {
                     "conditionMode": "llm",
                     "classifierPrompt": "按语气分流",
                     "branches": [
                         {"label": "愤怒投诉", "description": "强烈不满", "target": "msg-a"},
                         {"label": "普通咨询", "description": "平和询问", "target": "msg-b"},
                     ],
                     "defaultTarget": "msg-default",
                 }},
                _sink("msg-a", "A", "愤怒投诉"),
                _sink("msg-b", "B", "普通咨询"),
                _sink("msg-default", "默认", "默认分支"),
            ],
            "edges": [
                {"id": "e0", "source": "trigger-1", "target": "cond-1"},
                {"id": "e1", "source": "cond-1", "target": "msg-a"},
                {"id": "e2", "source": "cond-1", "target": "msg-b"},
                {"id": "e3", "source": "cond-1", "target": "msg-default"},
            ],
        }
    )


class _FakeLlmClassifier:
    """非离线分类器（冒充 LiteLLM 分类器）：本门只认 OfflineConditionClassifier。"""

    def classify(self, *, branches, context_text, instruction, node_id=None):
        return "普通咨询"


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


# --- U1011 prod 无 LLM：显式 failed，不静默走默认分支 ------------------------


def test_u1011_prod_offline_classifier_is_refused_with_a_machine_code(monkeypatch):
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_ENABLE_DEMO_MOCK", raising=False)
    with pytest.raises(ConditionClassifierUnavailable) as excinfo:
        run_graph(_llm_condition_graph(), condition_classifier=OfflineConditionClassifier())
    assert excinfo.value.code == "LLM_CLASSIFIER_UNAVAILABLE"
    assert excinfo.value.node_id == "cond-1"
    message = str(excinfo.value)
    # 排障要看到"该配什么"与"怎么开闸"，否则线上只会看到一句"失败了"。
    for token in ("LITELLM_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL",
                  "ATLAS_ENABLE_DEMO_MOCK=1"):
        assert token in message, f"文案缺少 {token}：{message}"


def test_u1012_prod_with_a_real_classifier_runs_unchanged(monkeypatch):
    """门只认离线档：真接了模型（任何非 OfflineConditionClassifier）照常分类与路由。"""
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_ENABLE_DEMO_MOCK", raising=False)
    result = run_graph(_llm_condition_graph(), condition_classifier=_FakeLlmClassifier())
    assert result["status"] == "completed"
    output = result["outputs"]["cond-1"]
    assert output["mode"] == "llm"
    assert output["branch"] == "普通咨询" and output["target"] == "msg-b"
    assert output["llm_errors"] == []
    assert "msg-b" in result["outputs"] and "msg-a" not in result["outputs"]


def test_u1013_prod_replay_scripted_classifier_is_not_gated(monkeypatch):
    """打包 V 回放不受本门影响：判据是分类器类型，脚本化分类器照常按录制标签路由。"""
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_ENABLE_DEMO_MOCK", raising=False)
    result = run_graph(
        _llm_condition_graph(),
        condition_classifier=ScriptedConditionClassifier({"cond-1": "愤怒投诉"}),
    )
    assert result["status"] == "completed"
    assert result["outputs"]["cond-1"]["target"] == "msg-a"


def test_u1014_demo_mock_flag_is_the_only_escape_hatch(monkeypatch):
    """prod + 显式 ATLAS_ENABLE_DEMO_MOCK=1：演示实例恢复 fail-safe 走默认分支。"""
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.setenv("ATLAS_ENABLE_DEMO_MOCK", "1")
    result = run_graph(_llm_condition_graph(), condition_classifier=OfflineConditionClassifier())
    assert result["status"] == "completed"
    output = result["outputs"]["cond-1"]
    assert output["target"] == "msg-default"
    assert "LLM 未配置" in output["llm_errors"][0]


def test_u1015_dev_profile_is_untouched(monkeypatch):
    """非 prod 零变化：本地/演示不配模型时离线 fail-safe 照旧（本门不误伤开发流程）。"""
    monkeypatch.setenv("ATLAS_ENV", "dev")
    monkeypatch.delenv("ATLAS_ENABLE_DEMO_MOCK", raising=False)
    result = run_graph(_llm_condition_graph(), condition_classifier=OfflineConditionClassifier())
    assert result["status"] == "completed"
    output = result["outputs"]["cond-1"]
    assert output["target"] == "msg-default"
    assert "LLM 未配置" in output["llm_errors"][0]


def test_u1016_run_failure_carries_code_and_node_id_to_the_client(monkeypatch):
    """终态归一化 + HTTP 形状：前端按码渲染双语文案，中文 message 仍是兜底真相。"""
    monkeypatch.setenv("ATLAS_ENV", "prod")
    monkeypatch.delenv("ATLAS_ENABLE_DEMO_MOCK", raising=False)
    exc = ConditionClassifierUnavailable("cond-1", "生产环境未配置 LLM 分类器")
    assert runtime_error_meta(exc) == {
        "errorCode": "LLM_CLASSIFIER_UNAVAILABLE",
        "errorParams": {"nodeId": "cond-1"},
    }
    response = condition_classifier_unavailable_handler(None, exc)
    assert isinstance(response, JSONResponse) and response.status_code == 500
    detail = json.loads(bytes(response.body))
    assert detail["detail"]["code"] == "LLM_CLASSIFIER_UNAVAILABLE"
    assert detail["detail"]["nodeId"] == "cond-1"
    assert "LLM_CLASSIFIER_UNAVAILABLE" in detail["detail"]["message"]
