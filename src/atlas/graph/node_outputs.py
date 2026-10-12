"""节点静态输出契约：可被 ``{{...}}`` 引用的输出根键的**后端唯一权威**（docs/14 D66）。

为什么有这个模块
----------------
同一套"静态节点输出根键"历史上有三份手抄，彼此漂移（D64/D66 的真实故障就是 loader 一直在
产出某键、而声明处漏了，导致画布补全给得出、编译期却以 REF_STATIC_PATH_NOT_FOUND 拒人）：

1. 后端 ``graph/dsl.py`` 的 L2 编译期引用复查表（运行期真拒人）；
2. 前端 ``lib/scope.ts`` 的 ``STATIC_OUTPUT_KEYS``（画布变量补全 / 前端校验）；
3. 前端 ``lib/schemas/nodes/*.schema.ts`` 各节点的 ``x-outputSchema.properties``。

本模块把**后端**收敛为单源：``dsl.py`` 不再自抄字面量，而是从 :data:`STATIC_NODE_OUTPUTS`
派生校验表；:func:`project_static_outputs` 是只读投影（reflection 风格的纯函数），供未来的
跨端 codegen / 契约端点 / 文档消费。

裁决口径
--------
根键集合以 **``graph/loader.py`` 各执行 mode 真实产出的并集**为权威，人工逐字抄录于此。
loader 产出按 mode 分散在 4000+ 行（初始字面量 + 大量 ``output["key"] = ...`` 动态赋值，且
部分来自 ``collaboration/event_waits`` 等协作者），当前结构下没有低成本、高保真的静态反射能
自动还原"各 mode 并集"，故不做脆弱的 AST 反射，改为：单源表 + 跨端对拍守护测试
（``tests/test_node_output_contract.py`` 把本投影与前端 ``scope.ts`` 逐字对拍，前端自身另有
vitest 对拍 scope.ts 与各 x-outputSchema），三份真相被两重测试锁死一致。

跨端 codegen（前端两份收口到由本投影生成的 TS 产物、不再手抄）是 D66 余部，涉及前端构建
约定，见 docs/14 D66 注记与 docs/08 决策记录。深层路径除 ``dsl.py`` 中 ai_decision 等显式
白名单外一律拒，不在本契约展开。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class NodeOutputContract(BaseModel):
    """单个静态节点类型的输出根键契约。

    roots 为该节点**各执行 mode 产出根键的并集**（无序语义，用 tuple 仅为可哈希/稳定投影）；
    顺序不参与裁决，但投影时按声明序输出，便于生成稳定的 codegen 产物与快照。
    """

    node_type: str
    roots: tuple[str, ...] = Field(default_factory=tuple)


# 后端唯一权威表。逐字取自 loader.py 各 mode 的真实产出（并集）；改动本表＝改编译期引用
# 复查的放行集，必须同步 loader 真实产出，并由跨端对拍测试守护与前端一致。
STATIC_NODE_OUTPUTS: dict[str, NodeOutputContract] = {
    contract.node_type: contract
    for contract in (
        NodeOutputContract(
            node_type="ai_decision",
            roots=("decision", "prompt_rendered", "memory_injected"),
        ),
        # 打包 AP（闭合 docs/14 D66）：以下三类此前只放行一小撮根键，而 loader 真产出更多。
        # wait 的 timeoutSeconds/onTimeout 是 config 键、不是产出，已排除。
        NodeOutputContract(
            node_type="condition",
            roots=(
                "mode", "branch", "target", "evaluation", "llm_errors",
                "expression_errors", "expressionErrorCodes", "expressionErrorParams",
            ),
        ),
        NodeOutputContract(
            node_type="loop",
            roots=(
                "mode", "index", "iterations", "items", "item", "results", "target",
                "exitReason", "expression_errors", "expressionErrorCodes",
                "expressionErrorParams",
            ),
        ),
        NodeOutputContract(
            node_type="parallel",
            roots=("status", "branches", "joinStrategy", "joinTarget"),
        ),
        NodeOutputContract(
            node_type="wait",
            roots=(
                "mode", "waitType", "durationMode", "durationExpression", "durationSeconds",
                "plannedDurationSeconds", "jitterSeconds", "absoluteTime", "eventKey",
                "eventKeys", "eventWaitMode", "matchedEventKey", "matchedEventKeys",
                "matchedPayloads", "receivedKeys", "signaled", "payload", "waitedSeconds",
                "resolvedBy", "token",
            ),
        ),
        NodeOutputContract(node_type="subgraph", roots=("status", "outputs")),
        NodeOutputContract(
            node_type="human_approval",
            roots=("decision", "target", "summary", "approver", "resolvedBy", "comment", "card"),
        ),
        # docs/109 打包 AB：三节点输出形状（与 ai_decision 同族）。
        NodeOutputContract(node_type="intent_recognition", roots=("result", "prompt_rendered")),
        NodeOutputContract(node_type="info_extraction", roots=("result", "prompt_rendered")),
        NodeOutputContract(node_type="content_generation", roots=("result", "prompt_rendered")),
        # 打包 AN②（闭合 D64）：state_machine 八条键逐字取自 loader 产出（camelCase，与
        # docs/123 §2.3 config／SSE 帧命名同族）；action 为工具产出、形状动态，深层仍拒。
        NodeOutputContract(
            node_type="state_machine",
            roots=("mode", "objectRef", "from", "to", "event", "state", "states", "action"),
        ),
    )
}


def static_output_keys() -> dict[str, tuple[str, ...]]:
    """派生 dsl L2 编译期复查用的 ``{节点类型: 根键 tuple}``（保持历史形状，消费点零改）。"""
    return {node_type: contract.roots for node_type, contract in STATIC_NODE_OUTPUTS.items()}


def project_static_outputs() -> dict[str, dict[str, list[str]]]:
    """只读投影：``{节点类型: {"roots": [...]}}``，供 codegen / 契约端点 / 文档消费。

    纯函数、无 IO、确定性（声明序稳定）；这是"后端 reflection 单源投影"的出口——前端与任何
    外部契约都应从这里取根键，而不是各自抄录。
    """
    return {
        node_type: {"roots": list(contract.roots)}
        for node_type, contract in STATIC_NODE_OUTPUTS.items()
    }


def project_for_node(node_type: str) -> dict[str, Any] | None:
    """单节点投影；未知节点类型返回 None（调用方据此区分"动态产出节点"与"拼写错误"）。"""
    contract = STATIC_NODE_OUTPUTS.get(node_type)
    return {"roots": list(contract.roots)} if contract is not None else None
