# -*- coding: utf-8 -*-
"""打包 AK（docs/122 D-3）：`ai_decision` 的可选记忆注入。

三条判据都围着"缺省即现状"排：
① 关闭时发给模型的 prompt 与运行产出**逐字不变**（黄金用例/回放/影子零回归）；
② 开启时注入段真的进了 prompt（不是只写进元数据）；
③ "没查到"与"没接线"必须在运行结果里分得开——这是 docs/63 以来反复修的静默降级族，
   所以断言的是 `status` 判别字段，不是"没抛异常"（后者空转即绿）。
"""

from __future__ import annotations

import pytest

from atlas.graph.dsl import GraphValidationError, parse_graph
from atlas.graph.loader import _memory_injection, run_graph
from atlas.llm.decision import AUTO_APPROVE


def _graph(config: dict | None = None) -> object:
    return parse_graph(
        {
            "version": 1,
            "variables": [
                {"name": "customer", "type": "string", "value": "张三", "scope": "global"}
            ],
            "nodes": [
                {
                    "id": "ai-1",
                    "type": "ai_decision",
                    "name": "决策",
                    "config": {"promptTemplate": "退款判断 {{global.customer}}", **(config or {})},
                }
            ],
            "edges": [],
        }
    )


class _RecordingDecisionClient:
    """记录真正发给模型的 prompt；置信度恒高，保证不触发低置信挂起分支。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def decide_refund(self, *, reason, amount, limit, prompt="", model=None):
        self.calls.append({"prompt": prompt, "reason": reason, "amount": amount, "limit": limit})
        return {"action": AUTO_APPROVE, "reason": "fake", "confidence": 1.0, "source": "fake"}


class _FakeMemoryRepo:
    def __init__(self, hits=None, raises: Exception | None = None) -> None:
        self.hits = hits if hits is not None else []
        self.raises = raises
        self.calls: list[tuple[str, dict]] = []

    def recall(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.raises is not None:
            raise self.raises
        return self.hits

    def list(self, **kwargs):  # noqa: ARG002  注入不该走 list，留着是为了暴露误用
        raise AssertionError("记忆注入不应调用 list()")


def _hit(memory_id: str, content: str) -> dict:
    return {"id": memory_id, "content": content, "score": 0.9}


# --------------------------------------------------------------------------- #
# U1334 缺省关闭＝逐字不变
# --------------------------------------------------------------------------- #


def test_u1334_injection_is_off_by_default_and_leaves_the_node_untouched():
    client = _RecordingDecisionClient()
    repo = _FakeMemoryRepo(hits=[_hit("mem-1", "不该被用上的记忆")])
    result = run_graph(_graph(), decision_client=client, memory_repo=repo)

    assert client.calls[0]["prompt"] == "退款判断 张三"
    assert repo.calls == [], "缺省关闭时一次召回都不该发生"
    output = result["outputs"]["ai-1"]
    assert "memory_injected" not in output, "关闭时产出不得多出新键（回放/黄金用例逐字不变）"
    assert output["prompt_rendered"] == "退款判断 张三"


def test_u1334b_explicit_enabled_false_behaves_identically():
    client = _RecordingDecisionClient()
    repo = _FakeMemoryRepo(hits=[_hit("mem-1", "不该被用上的记忆")])
    result = run_graph(
        _graph({"memoryInjection": {"enabled": False}}), decision_client=client, memory_repo=repo
    )
    assert client.calls[0]["prompt"] == "退款判断 张三"
    assert repo.calls == []
    assert "memory_injected" not in result["outputs"]["ai-1"]


# --------------------------------------------------------------------------- #
# U1335 开启＝真的进了 prompt
# --------------------------------------------------------------------------- #


def test_u1335_enabled_appends_the_recall_block_to_the_model_prompt():
    client = _RecordingDecisionClient()
    repo = _FakeMemoryRepo(hits=[_hit("mem-1", "该客户上次同类已退"), _hit("mem-2", "偏好先赔后退")])
    result = run_graph(
        _graph({"memoryInjection": {"enabled": True, "topK": 3}}),
        decision_client=client,
        memory_repo=repo,
    )

    sent = client.calls[0]["prompt"]
    assert sent.startswith("退款判断 张三")
    assert sent.endswith("【相关记忆】\n- 该客户上次同类已退\n- 偏好先赔后退"), sent
    # 运营看见的那份 prompt 必须就是发出去的那份（否则"看得懂"是假的）。
    assert result["outputs"]["ai-1"]["prompt_rendered"] == sent

    query, kwargs = repo.calls[0]
    assert query == "退款判断 张三", "缺省 query＝插值后的运营 prompt"
    assert kwargs["top_k"] == 3
    assert kwargs["scope"] is None, "缺省不按 scope 过滤（不从 globals 猜业务键）"

    meta = result["outputs"]["ai-1"]["memory_injected"]
    assert meta["count"] == 2 and meta["status"] == "ok" and meta["ids"] == ["mem-1", "mem-2"]


def test_u1335b_custom_query_is_interpolated_and_scope_is_passed_through():
    client = _RecordingDecisionClient()
    repo = _FakeMemoryRepo(hits=[_hit("mem-9", "张三的历史工单")])
    run_graph(
        _graph({
            "memoryInjection": {
                "enabled": True,
                "query": "{{global.customer}} 的历史",
                "minScore": 0.2,
                "scope": {"user_id": "u-1"},
            }
        }),
        decision_client=client,
        memory_repo=repo,
    )
    query, kwargs = repo.calls[0]
    assert query == "张三 的历史", "query 与 promptTemplate 同规：先插值"
    assert kwargs["scope"] == {"user_id": "u-1"}
    assert kwargs["min_score"] == 0.2
    assert "【相关记忆】" in client.calls[0]["prompt"]


def test_u1335c_top_k_is_clamped_where_the_dsl_is_not_the_gate():
    """运行期防线独立于编译期校验：脏值不能把召回放大成整库扫描。"""
    node = type(
        "_Node",
        (),
        {"id": "ai-1", "config": {"memoryInjection": {"enabled": True, "topK": 9999}}},
    )()
    repo = _FakeMemoryRepo(hits=[])
    _memory_injection(node, prompt="p", context={"global": {}}, memory_repo=repo)
    assert repo.calls[0][1]["top_k"] == 20


# --------------------------------------------------------------------------- #
# U1336 失败形状：没接线／存储故障／查到 0 条，三者必须互不相同
# --------------------------------------------------------------------------- #


def test_u1336_missing_store_degrades_visually_not_silently():
    client = _RecordingDecisionClient()
    result = run_graph(
        _graph({"memoryInjection": {"enabled": True}}), decision_client=client, memory_repo=None
    )
    assert client.calls[0]["prompt"] == "退款判断 张三", "没接线时不追加空段"
    meta = result["outputs"]["ai-1"]["memory_injected"]
    assert meta["status"] == "unavailable" and meta["count"] == 0
    assert result["status"] == "completed", "记忆是增强项，不该打断主流程"


def test_u1336b_store_failure_does_not_break_the_decision():
    client = _RecordingDecisionClient()
    repo = _FakeMemoryRepo(raises=RuntimeError("pg down"))
    result = run_graph(
        _graph({"memoryInjection": {"enabled": True}}), decision_client=client, memory_repo=repo
    )
    meta = result["outputs"]["ai-1"]["memory_injected"]
    assert meta["status"] == "error" and meta["count"] == 0
    assert client.calls[0]["prompt"] == "退款判断 张三"
    assert result["status"] == "completed"


def test_u1336c_zero_hits_is_a_different_answer_from_no_store():
    """这条是整批改判的落点：'没查到' 与 '没接线' 不能都长成"空结果"。"""
    client = _RecordingDecisionClient()
    repo = _FakeMemoryRepo(hits=[])
    result = run_graph(
        _graph({"memoryInjection": {"enabled": True}}), decision_client=client, memory_repo=repo
    )
    meta = result["outputs"]["ai-1"]["memory_injected"]
    assert meta["status"] == "ok" and meta["count"] == 0
    assert meta["status"] != "unavailable"


# --------------------------------------------------------------------------- #
# 编译期形状校验（两码，均带 zh/en 目录文案）
# --------------------------------------------------------------------------- #


def _codes_for(config: dict) -> list[str]:
    try:
        _graph(config)
    except GraphValidationError as exc:
        return exc.codes
    return []


def test_dsl_rejects_a_non_object_injection_setting():
    assert _codes_for({"memoryInjection": "yes"}) == ["NODE_AI_MEMORY_INJECTION_INVALID"]


@pytest.mark.parametrize(
    ("injection", "field", "expect"),
    [
        ({"enabled": "true"}, "enabled", "boolean"),
        ({"topK": 0}, "topK", "1-20"),
        ({"topK": 21}, "topK", "1-20"),
        ({"minScore": 1.5}, "minScore", "0-1"),
        ({"scope": {"user_id": 7}}, "scope", "object"),
        ({"query": 5}, "query", "string"),
    ],
)
def test_dsl_rejects_each_bad_injection_param_with_the_field_named(injection, field, expect):
    codes = _codes_for({"memoryInjection": injection})
    assert codes == ["NODE_AI_MEMORY_INJECTION_PARAM_INVALID"], codes
    try:
        _graph({"memoryInjection": injection})
    except GraphValidationError as exc:
        assert exc.errors[0] == f"memoryInjection.{field} 必须是 {expect}"
        params = exc.params[0]
        assert params["field"] == field and params["expect"] == expect
        assert params["owner"] == "ai-1", "owner 自动注入，前端才能定位到节点"
        location = next(item for item in exc.locations if item["index"] == 0)
        assert location["pointer"] == f"/memoryInjection/{field}", "指针不落到字段上，编辑器就点不出行"


def test_dsl_accepts_a_valid_injection_block_and_absence_of_it():
    """正控：合法形状与完全不配都必须过——否则上面那批红是假红。"""
    _graph({"memoryInjection": {"enabled": True, "query": "x", "topK": 5, "minScore": 0.1,
                                "scope": {"user_id": "u"}}})
    _graph({})
    _graph({"memoryInjection": {"enabled": True}})
