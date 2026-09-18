"""录制用例的 subgraph 引用快照冻结与内联优先解析（D26 部分取回）。

契约：04 §5.11（subgraph 快照内联段）、14 D26。

录制时父图整体已冻结为 ``RecordingCase.graph``；本模块递归冻结父图及嵌套
subgraph 节点引用的子图原始 JSON（深度上限同编译期 MAX_SUBGRAPH_DEPTH、
引用链防环、引用缺失不阻断录制）。单用例冻结回放时走「内联优先」resolver：
命中用例内快照即 parse 冻结物，未命中回退租户实时 GraphStore——使引用子图
在录制后被 reset/删除/改动，用例仍可按录制时形态回放。

注意分层：发布门禁（``gate.run_release_gate``）验证的是**当前 latest 草稿**，
子图必须取租户实时 store（草稿引用的子图被删应 block 发布），故门禁**不**使用
内联快照；内联只服务单用例冻结回放（replay 端点跑 ``case.graph``）。
"""

from __future__ import annotations

from typing import Any, Callable

from ..graph.dsl import MAX_SUBGRAPH_DEPTH, parse_graph

# 给定 subgraph graphId 引用原文（可能含 ``@N`` 钉版），返回子图原始 JSON；
# 缺失/不可解析返回 None。由 API 层基于租户 GraphStore 装配（recording 不碰存储）。
FetchRaw = Callable[[str], dict[str, Any] | None]


def collect_subgraph_snapshots(
    root_raw: dict[str, Any],
    *,
    fetch_raw: FetchRaw,
    max_depth: int = MAX_SUBGRAPH_DEPTH,
) -> dict[str, dict[str, Any]]:
    """递归收集 ``root_raw`` 及嵌套子图引用的原始 JSON，返回 ``{graphId 原文: raw}``。

    - key 为 subgraph 节点 config.graphId 引用原文（含 ``@N`` 钉版），与运行期
      graph_resolver 收到的入参逐字一致；
    - 深度超过 ``max_depth`` 的引用不冻结（非法深嵌由编译期拦截，快照无意义）；
    - 引用链（chain）防跨图环，已冻结的引用（snapshots）不重复抓取；
    - 引用缺失（fetch_raw 返回 None）跳过、不抛错，录制照常成功——该子图在
      回放期若无内联且实时 store 也缺失，折叠为 replay_status=failed 照现语义。
    """
    snapshots: dict[str, dict[str, Any]] = {}

    def walk(raw: dict[str, Any], depth: int, chain: tuple[str, ...]) -> None:
        for node in raw.get("nodes") or []:
            if not isinstance(node, dict) or node.get("type") != "subgraph":
                continue
            config = node.get("config") if isinstance(node.get("config"), dict) else {}
            ref = config.get("graphId")
            if not isinstance(ref, str) or not ref.strip():
                continue
            ref = ref.strip()
            if ref in snapshots or ref in chain:
                continue
            # 对齐 loader：depth+1 > MAX_SUBGRAPH_DEPTH 即非法嵌套，不冻结。
            if depth + 1 > max_depth:
                continue
            child_raw = fetch_raw(ref)
            if not isinstance(child_raw, dict):
                continue  # 引用缺失不阻断录制
            snapshots[ref] = child_raw
            walk(child_raw, depth + 1, (*chain, ref))

    walk(root_raw, 0, ())
    return snapshots


def inline_first_resolver(
    snapshots: dict[str, dict[str, Any]] | None,
    fallback: Callable[[str], Any],
) -> Callable[[str], Any]:
    """构造「内联快照优先、未命中回退 fallback（租户实时 store）」的 graph_resolver。

    旧用例 ``subgraphs={}`` 时等价于直接调用 fallback，保持历史行为。
    """

    frozen = snapshots or {}

    def resolve(graph_id: str):
        raw = frozen.get(graph_id)
        if raw is not None:
            return parse_graph(raw)
        return fallback(graph_id)

    return resolve
