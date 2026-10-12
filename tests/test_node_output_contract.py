"""docs/14 D66：节点静态输出根键"后端单源 + 跨端对拍 + 生成物新鲜度"守护（常跑，无集成依赖）。

历史教训（D64/D66）：同一套静态节点输出根键被抄了三份——后端 dsl L2 复查表、前端
lib/scope.ts 的 STATIC_OUTPUT_KEYS、前端各节点 x-outputSchema；loader 一直在产出的键在
声明处漏抄，就会"画布补全给得出、编译期 REF_STATIC_PATH_NOT_FOUND 拒人"。

本测试锁三层一致性（打包 AQ／ADR T35 后）：
  1. dsl 编译期复查表确实派生自 graph/node_outputs 单源（后端内部不再有第二份字面量）；
  2. 入仓生成物 schemas/generated/nodeOutputs.ts 与生成器渲染结果**逐字节相等**
     （忘重跑 / 手改产物都红；渲染实现脚本与测试共用，不存在第二份渲染器）；
  3. 后端单源投影与生成物的 STATIC_OUTPUT_KEYS **逐字一致**（跨语言两份）。
scope.ts 现为生成物的 re-export（不另抄表）；生成物与各 x-outputSchema 之间由前端
vitest（nodeSchemas.test.ts）对拍，三者闭环。形状权威 docs/124。
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

from atlas.graph import dsl
from atlas.graph.node_outputs import (
    STATIC_NODE_OUTPUTS,
    project_for_node,
    project_static_outputs,
    static_output_keys,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_SCOPE = REPO_ROOT / "frontend" / "src" / "lib" / "scope.ts"
GENERATED_NODE_OUTPUTS = (
    REPO_ROOT / "frontend" / "src" / "lib" / "schemas" / "generated" / "nodeOutputs.ts"
)
GENERATOR = REPO_ROOT / "scripts" / "dev" / "gen_node_outputs_ts.py"


def _load_generator():
    """导入生成器模块（scripts/dev 非安装包，按路径加载），测试与 CLI 共用同一 render()。"""
    spec = importlib.util.spec_from_file_location("gen_node_outputs_ts", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_projection_is_deterministic_and_covers_contract():
    projected = project_static_outputs()
    assert set(projected) == set(STATIC_NODE_OUTPUTS)
    for node_type, contract in STATIC_NODE_OUTPUTS.items():
        assert projected[node_type]["roots"] == list(contract.roots)
    # 根键非空、无重复（空表会让该节点引用被静默全放行/全拒，属配置事故）
    for node_type, payload in projected.items():
        roots = payload["roots"]
        assert roots, f"{node_type} 输出根键为空"
        assert len(roots) == len(set(roots)), f"{node_type} 输出根键重复"


def test_project_for_node_unknown_returns_none():
    assert project_for_node("not_a_node") is None
    assert project_for_node("condition") == {"roots": list(
        STATIC_NODE_OUTPUTS["condition"].roots)}


def test_dsl_table_is_derived_from_single_source():
    """dsl L2 复查表必须就是单源投影（同 tuple 内容），后端内部不得再藏一份字面量。"""
    assert dsl._STATIC_OUTPUT_KEYS == static_output_keys()
    for node_type, contract in STATIC_NODE_OUTPUTS.items():
        assert dsl._STATIC_OUTPUT_KEYS[node_type] == contract.roots


def test_generated_artifact_is_fresh_and_deterministic():
    """入仓生成物必须逐字节等于生成器当前渲染结果（U1368/U1370：忘重跑或手改产物都红）。"""
    generator = _load_generator()
    rendered = generator.render()
    # 确定性：同一投影连渲两次逐字节相等（U1367）。
    assert generator.render() == rendered
    assert GENERATED_NODE_OUTPUTS.exists(), (
        "生成物不存在；运行 .venv/bin/python scripts/dev/gen_node_outputs_ts.py"
    )
    on_disk = GENERATED_NODE_OUTPUTS.read_text(encoding="utf-8")
    assert on_disk == rendered, (
        "frontend/src/lib/schemas/generated/nodeOutputs.ts 已过期或被手改；"
        "改 graph/node_outputs.py 后必须运行 "
        ".venv/bin/python scripts/dev/gen_node_outputs_ts.py 重新生成"
    )


def _parse_frontend_static_table(text: str) -> dict[str, list[str]]:
    """从 scope.ts 文本解析 STATIC_OUTPUT_KEYS 对象（花括号配对 + 数组键提取）。"""
    start = text.index("STATIC_OUTPUT_KEYS")
    brace_open = text.index("{", start)
    depth = 0
    end = brace_open
    in_str: str | None = None
    for index in range(brace_open, len(text)):
        char = text[index]
        if in_str:
            if char == in_str and text[index - 1] != "\\":
                in_str = None
            continue
        if char in ("'", '"', "`"):
            in_str = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                end = index
                break
    block = text[brace_open:end]
    table: dict[str, list[str]] = {}
    for kind, array_body in re.findall(r"(\w+)\s*:\s*\[(.*?)\]", block, flags=re.S):
        table[kind] = re.findall(r"'([^']+)'", array_body)
    return table


@pytest.mark.skipif(
    not GENERATED_NODE_OUTPUTS.exists(), reason="frontend generated nodeOutputs.ts 不在当前检出中"
)
def test_backend_single_source_matches_frontend_scope_table():
    """后端单源投影与生成物 STATIC_OUTPUT_KEYS 节点集合、根键集合逐字一致（顺序无关）。

    打包 AQ 起表只活在生成物（scope.ts 仅 re-export）；对拍源随之切到生成物，
    新鲜度由 test_generated_artifact_is_fresh_and_deterministic 另守。
    """
    frontend = _parse_frontend_static_table(GENERATED_NODE_OUTPUTS.read_text(encoding="utf-8"))
    backend = project_static_outputs()

    assert set(frontend) == set(backend), (
        f"节点类型集合漂移：仅前端={sorted(set(frontend) - set(backend))}，"
        f"仅后端={sorted(set(backend) - set(frontend))}"
    )
    for node_type in backend:
        backend_roots = set(backend[node_type]["roots"])
        frontend_roots = set(frontend[node_type])
        assert backend_roots == frontend_roots, (
            f"{node_type} 输出根键漂移：仅后端={sorted(backend_roots - frontend_roots)}，"
            f"仅前端={sorted(frontend_roots - backend_roots)}"
        )
