"""打包 AB（docs/109）：三 LLM 结构化节点（意图识别/信息抽取/内容生成）契约测试。

U1269 DSL 校验 / U1270 无模型显式 FAILED / U1271-U1273 三节点执行 / U1274 非法形状兜底。
无模型 FAILED 是行为核心：三节点没有规则兜底，demo 面也不豁免（docs/109 §2.2）。
"""

from __future__ import annotations

import pytest

from atlas.graph.dsl import GraphDSL, GraphValidationError, NodeDSL, parse_graph
from atlas.graph.loader import run_graph, runtime_error_meta
from atlas.llm.structured import LLMStructuredUnavailable, classify_intent


def _three_node_graph():
    return parse_graph(
        {
            "version": 1,
            "variables": [],
            "nodes": [
                {"id": "trigger-1", "type": "trigger", "name": "触发",
                 "config": {"triggerType": "webhook", "webhookUrl": "/hooks/structured"}},
                {"id": "intent-1", "type": "intent_recognition", "name": "意图",
                 "config": {"intents": [{"name": "refund", "description": "退款"}, {"name": "inquiry"}],
                            "examples": ["我要退款"]}},
                {"id": "extract-1", "type": "info_extraction", "name": "抽取",
                 "config": {"fields": [{"name": "order_id", "type": "string", "description": "订单号"},
                                       {"name": "amount", "type": "number"}]}},
                {"id": "gen-1", "type": "content_generation", "name": "生成",
                 "config": {"template": "您的订单 {{extract-1.result.fields.order_id}} 处理中",
                            "style": "正式", "maxLength": 200}},
            ],
            "edges": [
                {"id": "e1", "source": "trigger-1", "target": "intent-1"},
                {"id": "e2", "source": "intent-1", "target": "extract-1"},
                {"id": "e3", "source": "extract-1", "target": "gen-1"},
            ],
        }
    )


class TestStructuredDslValidation:
    """U1269：三节点配置契约（缺必填/越界报 GraphValidationError）。"""

    def test_intent_requires_at_least_one_intent(self):
        with pytest.raises(GraphValidationError) as exc:
            parse_graph(
                {
                    "version": 1,
                    "variables": [],
                    "nodes": [
                        {"id": "trigger-1", "type": "trigger", "name": "触发",
                         "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
                        {"id": "intent-bad", "type": "intent_recognition", "name": "坏意图", "config": {}},
                    ],
                    "edges": [{"id": "e1", "source": "trigger-1", "target": "intent-bad"}],
                }
            )
        assert any("意图识别必须至少配置一个意图" in e for e in exc.value.errors)

    def test_extraction_requires_fields_and_valid_types(self):
        with pytest.raises(GraphValidationError) as exc:
            parse_graph(
                {
                    "version": 1,
                    "variables": [],
                    "nodes": [
                        {"id": "trigger-1", "type": "trigger", "name": "触发",
                         "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
                        {"id": "extract-bad", "type": "info_extraction", "name": "坏抽取",
                         "config": {"fields": [{"name": "x", "type": "date"}]}},
                    ],
                    "edges": [{"id": "e1", "source": "trigger-1", "target": "extract-bad"}],
                }
            )
        assert any("字段类型必须是" in e for e in exc.value.errors)

    def test_generation_requires_template_and_valid_max_length(self):
        with pytest.raises(GraphValidationError) as exc:
            parse_graph(
                {
                    "version": 1,
                    "variables": [],
                    "nodes": [
                        {"id": "trigger-1", "type": "trigger", "name": "触发",
                         "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
                        {"id": "gen-bad", "type": "content_generation", "name": "坏生成",
                         "config": {"template": "有内容", "maxLength": 99999}},
                    ],
                    "edges": [{"id": "e1", "source": "trigger-1", "target": "gen-bad"}],
                }
            )
        messages = exc.value.errors
        assert any("maxLength 必须是" in m for m in messages)


class TestStructuredNodesWithoutModel:
    """U1270：无可用模型 → 显式 FAILED（LLM_STRUCTURED_UNAVAILABLE），不静默降级。"""

    def test_intent_without_model_fails_hard(self, monkeypatch):
        monkeypatch.delenv("LITELLM_MODEL", raising=False)
        monkeypatch.setattr("atlas.llm.structured._resolve_llm_config", lambda tenant_id: None)
        with pytest.raises(LLMStructuredUnavailable) as excinfo:
            run_graph(_three_node_graph())
        assert excinfo.value.code == "LLM_STRUCTURED_UNAVAILABLE"
        assert excinfo.value.node_id == "intent-1"
        for token in ("LITELLM_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL"):
            assert token in str(excinfo.value), f"文案缺少 {token}"

    def _single_node_graph(self, node_type, node_id, config):
        return parse_graph(
            {
                "version": 1,
                "variables": [],
                "nodes": [
                    {"id": "trigger-1", "type": "trigger", "name": "触发",
                     "config": {"triggerType": "webhook", "webhookUrl": "/hooks/x"}},
                    {"id": node_id, "type": node_type, "name": node_type, "config": config},
                ],
                "edges": [{"id": "e1", "source": "trigger-1", "target": node_id}],
            }
        )

    def test_extraction_without_model_fails_hard(self, monkeypatch):
        monkeypatch.delenv("LITELLM_MODEL", raising=False)
        monkeypatch.setattr("atlas.llm.structured._resolve_llm_config", lambda tenant_id: None)
        with pytest.raises(LLMStructuredUnavailable) as excinfo:
            run_graph(self._single_node_graph(
                "info_extraction", "extract-1",
                {"fields": [{"name": "order_id", "type": "string"}]},
            ))
        assert excinfo.value.node_id == "extract-1"

    def test_generation_without_model_fails_hard(self, monkeypatch):
        monkeypatch.delenv("LITELLM_MODEL", raising=False)
        monkeypatch.setattr("atlas.llm.structured._resolve_llm_config", lambda tenant_id: None)
        with pytest.raises(LLMStructuredUnavailable) as excinfo:
            run_graph(self._single_node_graph(
                "content_generation", "gen-1",
                {"template": "模板", "maxLength": 200},
            ))
        assert excinfo.value.node_id == "gen-1"

    def test_unavailable_exception_shape(self):
        exc = LLMStructuredUnavailable("intent-1", "未配置 LLM 模型")
        assert exc.code == "LLM_STRUCTURED_UNAVAILABLE"
        assert exc.node_id == "intent-1"
        assert "LLM_STRUCTURED_UNAVAILABLE" in str(exc)
        assert runtime_error_meta(exc) == {
            "errorCode": "LLM_STRUCTURED_UNAVAILABLE",
            "errorParams": {"nodeId": "intent-1"},
        }


class TestStructuredNodesWithModel:
    """U1271-U1273：模型在位时三节点确定性执行（客户端函数 monkeypatch）。"""

    def test_intent_recognition_executes(self, monkeypatch):
        monkeypatch.setenv("LITELLM_MODEL", "demo-model")
        monkeypatch.setattr(
            "atlas.graph.loader.classify_intent",
            lambda **kwargs: {"intent": "refund", "confidence": 0.92, "slots": {"channel": "web"}},
        )
        result = run_graph(_three_node_graph())
        intent_out = result["outputs"]["intent-1"]
        assert result["status"] == "completed"
        assert intent_out["result"]["intent"] == "refund"
        assert intent_out["result"]["confidence"] == 0.92

    def test_info_extraction_executes(self, monkeypatch):
        monkeypatch.setenv("LITELLM_MODEL", "demo-model")
        monkeypatch.setattr(
            "atlas.graph.loader.classify_intent",
            lambda **kwargs: {"intent": "refund", "confidence": 0.9, "slots": {}},
        )
        monkeypatch.setattr(
            "atlas.graph.loader.extract_fields",
            lambda **kwargs: {"fields": {"order_id": "o-1", "amount": 299}, "missing": []},
        )
        result = run_graph(_three_node_graph())
        extract_out = result["outputs"]["extract-1"]
        assert result["status"] == "completed"
        assert extract_out["result"]["fields"]["order_id"] == "o-1"

    def test_content_generation_executes(self, monkeypatch):
        monkeypatch.setenv("LITELLM_MODEL", "demo-model")
        monkeypatch.setattr(
            "atlas.graph.loader.classify_intent",
            lambda **kwargs: {"intent": "refund", "confidence": 0.9, "slots": {}},
        )
        monkeypatch.setattr(
            "atlas.graph.loader.extract_fields",
            lambda **kwargs: {"fields": {"order_id": "o-1"}, "missing": []},
        )
        monkeypatch.setattr(
            "atlas.graph.loader.generate_content",
            lambda **kwargs: {"text": "您的订单 o-1 处理中", "prompt_rendered": "tpl"},
        )
        result = run_graph(_three_node_graph())
        gen_out = result["outputs"]["gen-1"]
        assert result["status"] == "completed"
        assert "o-1" in gen_out["result"]["text"]
        assert gen_out["prompt_rendered"] == "tpl"

    def test_text_source_resolves_context_variable(self, monkeypatch):
        """textSource 走 context 变量解析：intent 读到 global.raw_text，extract 读 intent 输出。"""
        monkeypatch.setenv("LITELLM_MODEL", "demo-model")
        seen: dict[str, object] = {}

        def fake_intent(**kwargs):
            seen["text"] = kwargs.get("text")
            return {"intent": "refund", "confidence": 0.9, "slots": {"channel": "web"}}

        monkeypatch.setattr("atlas.graph.loader.classify_intent", fake_intent)
        monkeypatch.setattr(
            "atlas.graph.loader.extract_fields",
            lambda **kwargs: {"fields": {}, "missing": ["order_id"]},
        )
        monkeypatch.setattr(
            "atlas.graph.loader.generate_content",
            lambda **kwargs: {"text": "x", "prompt_rendered": "y"},
        )
        graph = parse_graph(
            {
                "version": 1,
                "variables": [
                    {"name": "raw_text", "type": "string", "value": "原始文本", "scope": "global"}
                ],
                "nodes": [
                    {"id": "trigger-1", "type": "trigger", "name": "触发",
                     "config": {"triggerType": "webhook", "webhookUrl": "/hooks/structured"}},
                    {"id": "intent-1", "type": "intent_recognition", "name": "意图",
                     "config": {"intents": [{"name": "refund"}], "textSource": "{{global.raw_text}}"}},
                    {"id": "extract-1", "type": "info_extraction", "name": "抽取",
                     "config": {"fields": [{"name": "order_id", "type": "string"}],
                                "textSource": "{{intent-1.result.slots.channel}}"}},
                ],
                "edges": [
                    {"id": "e1", "source": "trigger-1", "target": "intent-1"},
                    {"id": "e2", "source": "intent-1", "target": "extract-1"},
                ],
            }
        )
        result = run_graph(graph)
        assert result["status"] == "completed"
        assert seen.get("text") == "原始文本"


class TestStructuredIllegalShapeFallback:
    """U1274：LLM 非法形状本地兜底（intent=None 等），节点照常 completed，不 FAILED。"""

    def test_intent_illegal_shape_is_not_failed(self, monkeypatch):
        monkeypatch.setenv("LITELLM_MODEL", "demo-model")
        monkeypatch.setattr(
            "atlas.graph.loader.classify_intent",
            lambda **kwargs: {"intent": None, "confidence": 0.0, "slots": {}},
        )
        result = run_graph(_three_node_graph())
        assert result["status"] == "completed"
        assert result["outputs"]["intent-1"]["result"]["intent"] is None
