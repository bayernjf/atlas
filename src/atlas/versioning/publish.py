"""发布动作：冻结 latest 草稿为不可变版本 + 递归钉版 subgraph 引用（M6，docs/20 §4.1 / ADR T19）。

发布语义：
- 只从 latest 草稿产新版本（releaseVersion 从 1 递增，已发布版本只读）；
- 图中每个 subgraph 节点的 `config.graphId` 解析后：子图已有发布版本则钉其最新版本号
  `graphId@vN`，否则先递归把子图草稿发布为 v1 再钉——版本号不可变故钉号即冻结引用内容；
- DSL 校验层已禁 subgraph 自引用/环/深度>3（04 §5.7），递归天然有界；`visiting` 仅为防御。

与存储解耦：只依赖 `GraphRepository` 协议（get/list_versions/publish），进程内/PG 后端同构。
"""

from __future__ import annotations

import copy
from typing import Any

from atlas.storage.base import GraphRepository

SUBSCRIPT_KEY = "@"


def publish(graph_store: GraphRepository, graph_id: str) -> int:
    """发布 latest 草稿为下一个版本，返回 releaseVersion。草稿不存在抛 KeyError。"""
    draft = graph_store.get(graph_id)
    if draft is None:
        raise KeyError(graph_id)
    pinned = _pin_subgraphs(graph_store, copy.deepcopy(draft), visiting=set())
    return graph_store.publish(graph_id, pinned)


def _pin_subgraphs(
    graph_store: GraphRepository,
    graph: dict[str, Any],
    *,
    visiting: set[str],
) -> dict[str, Any]:
    for node in graph.get("nodes", []):
        if not isinstance(node, dict) or node.get("type") != "subgraph":
            continue
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        ref = config.get("graphId")
        if not isinstance(ref, str) or not ref:
            continue
        sub_id = ref.split(SUBSCRIPT_KEY, 1)[0]
        if sub_id in visiting:
            continue  # 环（DSL 已禁，防御性跳过）
        versions = graph_store.list_versions(sub_id)
        if versions:
            pin = versions[-1]  # 钉最新发布版本
        else:
            visiting.add(sub_id)
            try:
                pin = publish(graph_store, sub_id)  # 递归发布子图草稿为 v1+
            finally:
                visiting.discard(sub_id)
        config["graphId"] = f"{sub_id}@{pin}"
    return graph
