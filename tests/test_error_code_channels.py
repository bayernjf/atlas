# -*- coding: utf-8 -*-
"""打包 AW（docs/89 A-4 的收口半）：错误码必须"到得了人"，不只是发得出来。

两条判据：
- **U1148 通道**：`SubgraphSuspendUnsupported` 此前在同步 `/run` 上没有异常处理器——
  SSE 有 `event: error` 帧承载，HTTP 侧则是一个没有 code 的裸 500，运营看到的是
  "Internal Server Error"，而"把该节点移到图顶层"这个可执行下一步只在日志里。
- **U1149/U1150 目录**：后端每一条结构化错误码，必须在前端三份目录之一里
  （`AUTH_ERROR_KEYS` / `runtime.json` / `validation.json` 的 `dsl`），否则必须出现在
  带理由的豁免表上。豁免表**不是垃圾桶**：每条都写明为什么现在不能译，
  其中"一个码承载多条不同答案"那几条已登记 docs/14 **D53**（先拆码或带 params）。

U1150 是这条守护的可信性证明：把缺陷植进**合成源码片段**（不动真文件），枚举器必须点名。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import atlas.api.main as api_main
from atlas.graph.loader import SubgraphSuspendUnsupported

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "atlas"
LOCALES = REPO / "frontend" / "src" / "locales"

client = TestClient(api_main.app)

# --------------------------------------------------------------------------- U1149


def _catalog_keys() -> tuple[set[str], set[str], set[str]]:
    """前端三份目录的键集合（认证码 / 运行期码 / 编译期 dsl 码）。"""
    auth = set(
        re.findall(
            r"^\s*(AUTH_[A-Z_]+):",
            (REPO / "frontend" / "src" / "lib" / "apiClient.ts").read_text(encoding="utf-8"),
            re.M,
        )
    )
    runtime = set(json.loads((LOCALES / "zh-CN" / "runtime.json").read_text(encoding="utf-8")))
    validation = set(
        json.loads((LOCALES / "zh-CN" / "validation.json").read_text(encoding="utf-8"))["dsl"]
    )
    return auth, runtime, validation


# 结构化通道的五种发射形状：
#   1) detail={"code": "X", ...}            —— REST / SSE 终态
#   2) code = "X"                           —— 异常类属性，经 exception_handler 或
#                                              runtime_error_meta 变成上面的形状
#   3) code="X"（add / add_graph 的关键字参）—— 编译期 422 的 codes[]
#   4) StructuredError("X", ...)            —— 工具/适配器结构化错误（节点产出）
#   5) "error_code": "X"                    —— 同上，写进结果字典的形状
_SHAPES = (
    re.compile(r"""["']code["']\s*:\s*["']([A-Z][A-Z0-9_]{3,})["']"""),
    re.compile(r"""^\s*code\s*=\s*["']([A-Z][A-Z0-9_]{3,})["']""", re.M),
    re.compile(r"""\bcode\s*=\s*["']([A-Z][A-Z0-9_]{3,})["']"""),
    re.compile(r"""StructuredError\(\s*["']([A-Z][A-Z0-9_]{3,})["']"""),
    re.compile(r"""["']error_code["']\s*:\s*["']([A-Z][A-Z0-9_]{3,})["']"""),
)

# 豁免表：每条都要写"为什么现在不能译"。加了新码而进这张表，等于把 A-4 重新欠一次。
TOOL_OUTPUT_ONLY = {
    # 只出现在工具/适配器结果的 error_code 里，UI 以机器码呈现（Monitoring 的 error_codes
    # 列、节点产出块）。docs/57 §2.5：节点产出＝业务数据，不译；译它等于把适配器日志塞进
    # i18n 目录。这一类是**长期口径**，不是待办（待办只有下面 COARSE_CODE 那一组）。
    "AUTH_FAILED",  # shop/adapter.py
    "BROWSER_NOT_STARTED",  # web/adapter.py
    "CHANNEL_INVALID_PARAMETER",
    "DB_SQL_NOT_READ_ONLY",
    "ELEMENT_NOT_FOUND",
    "HTTP_CIRCUIT_OPEN",
    "INVALID_ACTION",
    "MEMORY_INVALID_INPUT",
    "MEMORY_NOT_CONFIGURED",
    "MISSING_PARAMETER",  # logistics/database/httpapi 适配器共用这一个名字
    "OPENAPI_CREDENTIAL_MISSING",
    "ORDER_NOT_FOUND",
    "PERMISSION_DENIED",
    "SECRET_DECRYPT_ERROR",
    "SECRET_UNAVAILABLE",
    "STORAGE_ERROR",
    "UNKNOWN_CAPABILITY",
}
COARSE_CODE = {
    # 一个码承载多条**不同**答案，按码出模板会丢掉诊断信息（甚至误导）。
    # 修法是先拆码或补 params，再补文案——已登记 docs/14 D53，不是"以后再说"。
    "INVALID_PARAMETER",
    "NOT_FOUND",
    "OPENAPI_INVALID_CREDENTIAL",
    "OPENAPI_INVALID_DOCUMENT",
    "WAIT_EVENT_PAYLOAD_INVALID",
}
DEAD_LETTER_REASON = {
    # webhook 死信台账的逐条原因码（`channels/webhooks.py` 落库成
    # `reasons:[{graphId, code}]`，UI 在死信列表里以机器码呈现，docs/39）。
    # 它是**已入库的历史记录**、不是当次请求的错误：同一码对不同 graphId 成立，
    # 逐条译文要连 graphId 一起排；按 docs/57 §2.5 属"业务数据不译"。
    "MISSING_GRAPH_ID",
    "NO_PUBLISHED_VERSION",
    "RESOLVE_FAILED",
    "TRIGGER_FAILED",
}
EXEMPT = TOOL_OUTPUT_ONLY | COARSE_CODE | DEAD_LETTER_REASON


def emitted_codes(text: str) -> set[str]:
    found: set[str] = set()
    for shape in _SHAPES:
        found.update(shape.findall(text))
    return found


def structured_codes() -> dict[str, list[str]]:
    """码 → 发射位置（相对路径）。"""
    where: dict[str, list[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for code in emitted_codes(path.read_text(encoding="utf-8")):
            where.setdefault(code, []).append(str(path.relative_to(REPO)))
    return where


def test_u1149_every_structured_code_has_copy_or_a_reason():
    auth, runtime, validation = _catalog_keys()
    known = auth | runtime | validation
    where = structured_codes()
    untranslated = sorted(
        code for code in where if code not in known and code not in EXEMPT
    )
    assert untranslated == [], (
        "这些结构化错误码前端没有任何文案，也不在带理由的豁免表上："
        f"{[(c, where[c][0]) for c in untranslated]}；"
        "补 zh/en 键（runtime.json 或 validation.json 的 dsl 块），"
        "或把它加进豁免表并写明为什么现在不能译"
    )


def test_u1149_allowlist_is_not_quietly_stale():
    """豁免表里的码必须**仍然**发得出来；码被删掉/改名后还留着＝白占一个免检位。"""
    where = set(structured_codes())
    stale = sorted(code for code in EXEMPT if code not in where)
    assert stale == [], f"豁免表有过期条目，删掉它们：{stale}"


def test_u1150_reverse_gate_the_enumerator_sees_a_planted_code():
    """反向门：合成源码里植一条新码，枚举器必须抓到；守护必须因此变红。

    不动真文件（那是 tampering），改判"枚举器看得见吗"＋"目录里没有就判红"两件事，
    合起来等价于"将来有人加一条没文案的码，这条守护会红"。
    """
    snippet = (
        'raise HTTPException(\n    status_code=422,\n'
        '    detail={"code": "AW_PLANTED_CODE", "message": "植进来的"},\n)\n'
    )
    assert "AW_PLANTED_CODE" in emitted_codes(snippet), "枚举器对 detail 形状失明＝守护是假的"
    auth, runtime, validation = _catalog_keys()
    assert "AW_PLANTED_CODE" not in auth | runtime | validation
    assert "AW_PLANTED_CODE" not in EXEMPT

    # 同一条形状在真实目录里的对照组：AW 批补的码确实被认出来了。
    assert emitted_codes('detail={"code": "OPENAPI_DUPLICATE", "message": "x"}') == {
        "OPENAPI_DUPLICATE"
    }


def test_u1150_shapes_the_enumerator_covers_are_the_five_real_ones():
    """五种发射形状各测一条正例，防"只测了自己最顺手的那一种"。"""
    assert emitted_codes('    code = "AW_A_CLASS"') == {"AW_A_CLASS"}
    assert emitted_codes('        add(f"…", "/model", code="AW_B_DSL")') == {"AW_B_DSL"}
    assert emitted_codes('detail={"code": "AW_C_DETAIL", "message": m}') == {"AW_C_DETAIL"}
    assert emitted_codes('return ActionResult.failed(StructuredError("AW_D_TOOL", "中文"))') == {
        "AW_D_TOOL"
    }
    assert emitted_codes('result = {"ok": False, "error_code": "AW_E_DICT"}') == {"AW_E_DICT"}


# --------------------------------------------------------------------------- U1148


def _login() -> dict[str, str]:
    resp = client.post(
        "/api/auth/login", json={"username": "admin-a", "password": "admin123"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['token']}"}


def test_u1148_sync_run_reports_subgraph_suspend_as_a_structured_500(monkeypatch) -> None:
    """同步 /run 必须给出 code＋nodeId，而不是 Starlette 的裸 500。

    `run_graph` 用桩替代：真 raise 需要 PG 档＋子图内审批节点（跨后端装配），
    而**缺的正是 HTTP 层的接线**，不是 loader 的抛出——抛出侧由
    `tests/test_subgraph_suspend_zk.py` 的 6 例钉住。
    """
    headers = _login()
    graph = {
        "name": "u1148-sync-run",
        "nodes": [
            {"id": "trigger-1", "type": "trigger", "name": "触发",
             "config": {"triggerType": "webhook", "webhookUrl": "/hooks/u1148"}},
            {"id": "tool_call-1", "type": "tool_call", "name": "工具",
             "config": {"tool": "memory/write", "params": {"content": "u1148"}}},
        ],
        "edges": [{"id": "e1", "source": "trigger-1", "target": "tool_call-1"}],
    }
    saved = client.post("/api/graphs", json=graph, headers=headers)
    assert saved.status_code == 200, saved.text
    graph_id = saved.json()["id"]

    def _boom(*_a, **_k):
        raise SubgraphSuspendUnsupported("approve-1", "子图内挂起点不可跨进程恢复")
    monkeypatch.setattr(api_main, "run_graph", _boom)
    resp = client.post(f"/api/graphs/{graph_id}/run", json={}, headers=headers)

    assert resp.status_code == 500, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, dict), (
        f"裸 500 的 detail 是字符串＝没有 code，运营无法按码分流：{detail!r}"
    )
    assert detail["code"] == "SUBGRAPH_SUSPEND_UNSUPPORTED"
    assert detail["nodeId"] == "approve-1"
    assert "子图" in detail["message"]


def test_u1148_the_code_is_registered_in_the_runtime_catalog_so_the_copy_can_render() -> None:
    """码发得出还不够——它得在前端目录里，否则英文态永远是一句中文（A-4 的根因）。"""
    _auth, runtime, _validation = _catalog_keys()
    assert "SUBGRAPH_SUSPEND_UNSUPPORTED" in runtime
    assert "OPENAPI_DUPLICATE" in runtime
    # 前缀白名单时代这两类会被判成"不可译"，目录成员判定才把它们放进来。
    assert not re.match(r"^(COND_|WAIT_|LOOP_|FOREACH_|LLM_|RUNTIME_)", "SUBGRAPH_SUSPEND_UNSUPPORTED")


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_u1149_allowlisted_codes_have_a_written_reason(locale: str) -> None:
    """豁免不是沉默的：每条都要在测试里带上理由，且两档目录都不能"顺手"有它的键。"""
    runtime = json.loads((LOCALES / locale / "runtime.json").read_text(encoding="utf-8"))
    validation = json.loads((LOCALES / locale / "validation.json").read_text(encoding="utf-8"))["dsl"]
    for code in EXEMPT:
        assert code not in runtime and code not in validation, (
            f"{locale}:{code} 已在豁免表上却又有文案——把豁免条目删掉，否则它躲过了未来的核对"
        )
    groups = [TOOL_OUTPUT_ONLY, COARSE_CODE, DEAD_LETTER_REASON]
    assert len({c for g in groups for c in g}) == sum(len(g) for g in groups), (
        "三组豁免不许重叠：一条码只该有一个「为什么不译」的理由"
    )
