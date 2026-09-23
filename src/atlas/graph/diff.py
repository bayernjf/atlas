"""两版 Graph JSON 结构差异（B3，D26「跨版本/配置更新差异对比」子集）。

纯函数、零新依赖；输入为前端 ``graphSerializer`` 产物（version 1，形状见
``graph/dsl.py`` GraphDSL：variables/nodes/config/edges）。输出结构化差异，
供发布流回答「这次发布相对上一版改了什么」。

边界：只做**配置结构** diff（节点/边/变量的增删改）；不做运行结果/语义 diff
（那属录制回放 D26 的批量回放），不做节点位置（position）等纯画布布局 diff。
"""

from __future__ import annotations

from typing import Any


def _index(items: list[Any], key: str) -> dict[str, dict[str, Any]]:
    """把 [{key: value, ...}] 按 key 建索引；跳过非 dict 或缺 key 的项。"""
    result: dict[str, dict[str, Any]] = {}
    for item in items or []:
        if isinstance(item, dict) and item.get(key) is not None:
            result[item[key]] = item
    return result


def _changed_config_keys(base: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    """节点 config 的键级差异：新增/删除/值变的键名（升序）。"""
    keys = set(base or {}) | set(candidate or {})
    return sorted(k for k in keys if (base or {}).get(k) != (candidate or {}).get(k))


def diff_graph(base: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    """对比两版 graph JSON，返回结构化差异。

    返回::

        {
          "nodes": {"added": [id], "removed": [id],
                    "changed": [{"id", "changes": [{"field", ...}]}]},
          "edges": {"added": [id], "removed": [id]},
          "variables": {"added": [name], "removed": [name], "changed": [name]},
        }
    """
    base = base or {}
    candidate = candidate or {}

    # ---- 节点 ----
    bnodes = _index(base.get("nodes", []), "id")
    cnodes = _index(candidate.get("nodes", []), "id")
    nodes_added: list[str] = []
    nodes_removed: list[str] = []
    nodes_changed: list[dict[str, Any]] = []
    for nid, cnode in cnodes.items():
        bnode = bnodes.get(nid)
        if bnode is None:
            nodes_added.append(nid)
            continue
        changes: list[dict[str, Any]] = []
        if bnode.get("type") != cnode.get("type"):
            changes.append(
                {"field": "type", "from": bnode.get("type"), "to": cnode.get("type")}
            )
        if bnode.get("name") != cnode.get("name"):
            changes.append(
                {"field": "name", "from": bnode.get("name"), "to": cnode.get("name")}
            )
        config_keys = _changed_config_keys(bnode.get("config", {}), cnode.get("config", {}))
        if config_keys:
            changes.append({"field": "config", "configKeys": config_keys})
        if changes:
            nodes_changed.append({"id": nid, "changes": changes})
    for nid in bnodes:
        if nid not in cnodes:
            nodes_removed.append(nid)

    # ---- 边（按 id；位置布局不参与）----
    bedges = _index(base.get("edges", []), "id")
    cedges = _index(candidate.get("edges", []), "id")
    edges_added = sorted(eid for eid in cedges if eid not in bedges)
    edges_removed = sorted(eid for eid in bedges if eid not in cedges)

    # ---- 变量（按 name）----
    bvars = _index(base.get("variables", []), "name")
    cvars = _index(candidate.get("variables", []), "name")
    vars_added: list[str] = []
    vars_removed: list[str] = []
    vars_changed: list[str] = []
    for name, cv in cvars.items():
        bv = bvars.get(name)
        if bv is None:
            vars_added.append(name)
        elif bv.get("value") != cv.get("value") or bv.get("type") != cv.get("type"):
            vars_changed.append(name)
    for name in bvars:
        if name not in cvars:
            vars_removed.append(name)

    return {
        "nodes": {
            "added": sorted(nodes_added),
            "removed": sorted(nodes_removed),
            "changed": nodes_changed,
        },
        "edges": {"added": edges_added, "removed": edges_removed},
        "variables": {
            "added": sorted(vars_added),
            "removed": sorted(vars_removed),
            "changed": sorted(vars_changed),
        },
    }


def diff_summary(diff: dict[str, Any]) -> dict[str, int]:
    """把 diff 折叠为各项计数，供一眼判断是否有实质改动。"""
    n, e, v = diff["nodes"], diff["edges"], diff["variables"]
    return {
        "nodesAdded": len(n["added"]),
        "nodesRemoved": len(n["removed"]),
        "nodesChanged": len(n["changed"]),
        "edgesAdded": len(e["added"]),
        "edgesRemoved": len(e["removed"]),
        "variablesAdded": len(v["added"]),
        "variablesRemoved": len(v["removed"]),
        "variablesChanged": len(v["changed"]),
    }


def has_changes(diff: dict[str, Any]) -> bool:
    """diff 是否含任意差异（全 0 即两版结构一致）。"""
    return any(diff_summary(diff).values())
