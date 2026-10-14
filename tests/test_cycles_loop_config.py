"""图级 loop 配置的编译期校验（docs/130 打包 BN＝D69；原子④ U1434 的一半）。

只钉**校验**与缺省兼容：启用面缺省 `None` ⇒ 现状逐字不变（C-D2）。
运行行为（圈真跑起来）由 tests/test_cycles_package.py 覆盖。
"""

from __future__ import annotations

import pytest

from atlas.graph.dsl import GraphDSL, GraphValidationError, parse_graph


def _graph(loop=None) -> dict:
    body = {
        "version": 1,
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "config": {"triggerType": "manual"}},
            {"id": "tool-1", "type": "tool_call", "name": "干活",
             "config": {"tool": "http/request", "params": '{"url":"/x"}'}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool-1"}],
    }
    if loop is not None:
        body["loop"] = loop
    return body


def _codes(body: dict) -> list[str]:
    with pytest.raises(GraphValidationError) as excinfo:
        parse_graph(body)
    return list(excinfo.value.codes)


# --- 缺省兼容（C-D2）----------------------------------------------------------


def test_loop_absent_keeps_today_behaviour():
    graph = parse_graph(_graph())
    assert graph.loop is None
    assert graph.model_dump() == parse_graph(_graph()).model_dump()


def test_loop_none_is_same_as_absent():
    assert parse_graph(_graph(None)).loop is None


# --- 合法配置 ----------------------------------------------------------------


def test_valid_loop_config_parses_and_keeps_shape():
    loop = {
        "enabled": True,
        "maxIterations": 5,
        "maxWallClockSeconds": 600,
        "maxTokens": 50000,
        "completeWhen": "{{tool-1.result.status}} == 'SUCCESS'",
        "judgeMode": "rule",
        "carry": {"order_id": "{{trigger-1.context.payload.order_id}}"},
        "intervalSeconds": 30,
    }
    graph = parse_graph(_graph(loop))
    assert graph.loop["maxIterations"] == 5
    assert graph.loop["carry"]["order_id"].startswith("{{")


def test_llm_judge_mode_requires_prompt():
    assert "LOOP_CONFIG_INVALID" in _codes(_graph({"judgeMode": "llm"}))
    # 给了 prompt 就合法
    parse_graph(_graph({"judgeMode": "llm", "judgePrompt": "达成了吗？"}))


def test_empty_loop_object_is_legal():
    """`loop: {}`＝启用但全用缺省（与 None 不同：None 是不启用）。"""
    assert parse_graph(_graph({})).loop == {}


# --- 非法配置（U1434）--------------------------------------------------------


@pytest.mark.parametrize(
    ("loop", "why"),
    [
        ({"maxIterations": 0}, "迭代数下界"),
        ({"maxIterations": 51}, "迭代数上界"),
        ({"maxIterations": "5"}, "迭代数类型"),
        ({"maxIterations": True}, "bool 不算整数"),
        ({"maxWallClockSeconds": 29}, "墙钟下界"),
        ({"maxWallClockSeconds": 86401}, "墙钟上界"),
        ({"maxTokens": 0}, "token 下界"),
        ({"intervalSeconds": 3601}, "间隔上界"),
        ({"enabled": "yes"}, "enabled 必须是布尔"),
        ({"judgeMode": "vibes"}, "judgeMode 枚举"),
        ({"completeWhen": "1 + 2"}, "completeWhen 顶层非布尔"),
        ({"completeWhen": "{{a}} +"}, "completeWhen 语法错"),
        ({"completeWhen": 42}, "completeWhen 类型"),
        ({"carry": {"": "x"}}, "carry 空键"),
        ({"carry": {"k": 5}}, "carry 值必须是字符串"),
        ({"carry": ["k"]}, "carry 必须是对象"),
    ],
)
def test_invalid_loop_config_is_rejected_with_one_code(loop, why):
    codes = _codes(_graph(loop))
    assert "LOOP_CONFIG_INVALID" in codes, why


def test_loop_wrong_type_fails_at_dsl_parse():
    """`loop` 传字符串 ⇒ pydantic 层拒绝（`DSL_PARSE_FAILED`），不是本层的 LOOP_CONFIG_INVALID。

    图级配置的类型由 GraphDSL 的注解守（`dict[str, Any] | None`），本层只管**内容**——
    这与 `globalErrorHandler`／`selfHealing` 的既有分工同形。
    """
    assert "DSL_PARSE_FAILED" in _codes(_graph("not-an-object"))


def test_pointer_reaches_the_message_about_the_offending_field():
    """图级 issue 的定位以 pointer 字符串进 messages 侧（06 §6.13：图级无 nodeId）。

    钉文案里带字段名，是为了让运营看得见"是哪个字段坏了"，而不是只看到一个笼统的码。
    """
    with pytest.raises(GraphValidationError) as excinfo:
        parse_graph(_graph({"maxIterations": 999}))
    assert any("maxIterations" in message for message in excinfo.value.errors)
