# -*- coding: utf-8 -*-
"""发布前子图版本升级体检（docs/28 §5.2 ⑪，D21 部分取回）。

发布前告诉操作者「本次发布将把哪些顶层 subgraph 引用钉到什么版本、相对当前已发布
版本钉的是升级还是首次钉版」，引导先跑发布门禁回归。**纯只读**：不产版本、不写状态、
不自动改草稿、不阻断发布、不做 pin 编辑器；v1 只扫顶层 subgraph 节点（嵌套子图随其
直接父图发布时体检，不递归），且只对已发布版本号，不检测子图未发布草稿的 dirty。

与存储解耦：只依赖 ``GraphRepository`` 的 get / list_versions（与
:mod:`atlas.versioning.publish` 同一协议，进程内 / PG 后端同构）。
"""

from __future__ import annotations

from typing import Any, TypedDict

from atlas.storage.base import GraphRepository

SUBSCRIPT_KEY = "@"


class SubgraphUpgrade(TypedDict):
    """单个顶层 subgraph 引用的发布前版本变化。"""

    node_id: str
    sub_id: str
    from_version: int | None  # 父图最新发布版所钉 @N；无（首次钉版）为 None
    to_version: int  # 本次发布将钉到的版本（子图从无发布版则为 1，发布期递归发 v1）
    first_pin: bool  # 该引用首次被钉版（父图首次发布 / 节点新增 / 子图从无发布版）


def split_graph_ref(ref: Any) -> tuple[str | None, int | None]:
    """解析 ``config.graphId``：裸 id / ``id@N`` / ``id@draft`` → ``(sub_id, pinned)``。

    裸 id 或非数字后缀（如 ``@draft``）的 pinned 为 None；空值/非字符串返 (None, None)。
    与 publish._pin_subgraphs 的 ``ref.split("@", 1)[0]`` 同口径取 sub_id。
    """
    if not isinstance(ref, str) or not ref:
        return None, None
    if SUBSCRIPT_KEY in ref:
        sub_id, tail = ref.split(SUBSCRIPT_KEY, 1)
        pinned = int(tail) if tail.strip().isdigit() else None
    else:
        sub_id, pinned = ref, None
    sub_id = sub_id.strip()
    return (sub_id or None), pinned


def _released_pins(snapshot: dict[str, Any]) -> dict[str, int]:
    """父图已发布快照中 node_id → 所钉 ``@N``（裸 id / @draft 不计入）。"""
    pins: dict[str, int] = {}
    for node in snapshot.get("nodes", []):
        if not isinstance(node, dict) or node.get("type") != "subgraph":
            continue
        node_id = node.get("id")
        if not isinstance(node_id, str):
            continue
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        _, pinned = split_graph_ref(config.get("graphId"))
        if pinned is not None:
            pins[node_id] = pinned
    return pins


def subgraph_upgrade_plan(
    graph_store: GraphRepository, graph_id: str
) -> list[SubgraphUpgrade] | None:
    """计算发布前子图升级清单；草稿不存在返 None（端点据此 404）。

    - 取 latest 草稿，只扫顶层 ``type == "subgraph"`` 节点（不递归）；
    - ``to_version``＝子图最新发布版号，子图从无发布版则 1、标记首次钉版（发布期
      ``publish`` 会递归先把该子图草稿发 v1，现状机制不变）；
    - ``from_version``＝父图最新发布快照中同 node_id 所钉 @N；父图从无发布版或该节点
      上版不存在/为裸引用则 None；
    - 仅列首次钉版或版本有变化的项（from == to 且非首次钉版不列）。
    """
    draft = graph_store.get(graph_id)
    if draft is None:
        return None

    parent_versions = graph_store.list_versions(graph_id)
    parent_first_release = not parent_versions
    released_pins: dict[str, int] = {}
    if parent_versions:
        snapshot = graph_store.get(graph_id, parent_versions[-1])
        if snapshot is not None:
            released_pins = _released_pins(snapshot)

    plan: list[SubgraphUpgrade] = []
    for node in draft.get("nodes", []):
        if not isinstance(node, dict) or node.get("type") != "subgraph":
            continue  # 只扫顶层，嵌套子图不递归
        node_id = node.get("id")
        if not isinstance(node_id, str):
            continue
        config = node.get("config") if isinstance(node.get("config"), dict) else {}
        sub_id, _draft_pinned = split_graph_ref(config.get("graphId"))
        if sub_id is None:
            continue

        sub_versions = graph_store.list_versions(sub_id)
        if sub_versions:
            to_version = sub_versions[-1]
            sub_never_published = False
        else:
            to_version = 1
            sub_never_published = True

        from_version = None if parent_first_release else released_pins.get(node_id)
        first_pin = parent_first_release or sub_never_published or from_version is None
        if first_pin or from_version != to_version:
            plan.append(
                {
                    "node_id": node_id,
                    "sub_id": sub_id,
                    "from_version": from_version,
                    "to_version": to_version,
                    "first_pin": bool(first_pin),
                }
            )
    return plan
