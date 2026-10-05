# -*- coding: utf-8 -*-
"""打包 AW（docs/89 A-4 的收口半）：错误码必须"到得了人"，不只是发得出来。

三条判据：
- **U1148 通道**：`SubgraphSuspendUnsupported` 此前在同步 `/run` 上没有异常处理器——
  SSE 有 `event: error` 帧承载，HTTP 侧则是一个没有 code 的裸 500，运营看到的是
  "Internal Server Error"，而"把该节点移到图顶层"这个可执行下一步只在日志里。
- **U1149 目录**：后端每一条结构化错误码，必须在前端三份目录之一里
  （`AUTH_ERROR_KEYS` / `runtime.json` / `validation.json` 的 `dsl`），否则必须出现在
  带理由的豁免表上。
- **U1151 豁免表不腐烂**：豁免表**不是垃圾桶**——每条都得写明为什么现在不能译，条目不
  许过期（码不再发出就必须删），三组理由之间不许重叠（一条码只允许一个理由），
  且豁免中的码不能"顺手"又有文案。其中"一个码承载多条不同答案"那几条已登记
  docs/14 **D53**（先拆码或带 params）。

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

# ---------------------------------------------------------- U1149 / U1150 / U1151


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


# 结构化通道的发射形状：
#   1) detail={"code": "X", ...}            —— REST / SSE 终态
#   2) code = "X"                           —— 异常类属性，经 exception_handler 或
#                                              runtime_error_meta 变成上面的形状
#   3) code="X"（add / add_graph 的关键字参）—— 编译期 422 的 codes[]
#   4) StructuredError("X", ...)            —— 工具/适配器结构化错误（节点产出）
#   5) "error_code": "X"                    —— 同上，写进结果字典的形状
#   6) XxxError("CODE", ...)                —— 异常构造调用，类名以 Error 结尾
#   7) XxxSchema("CODE", ...)               —— 同上，但类名以 Schema 结尾（`UnsupportedSchema`）
#   8) code: T = "CODE"                     —— 带类型标注的默认值（`_credential_error` 未知方案码）
_SHAPES = (
    re.compile(r"""["']code["']\s*:\s*["']([A-Z][A-Z0-9_]{3,})["']"""),
    re.compile(r"""^\s*code\s*=\s*["']([A-Z][A-Z0-9_]{3,})["']""", re.M),
    re.compile(r"""\bcode\s*=\s*["']([A-Z][A-Z0-9_]{3,})["']"""),
    re.compile(r"""StructuredError\(\s*["']([A-Z][A-Z0-9_]{3,})["']"""),
    re.compile(r"""["']error_code["']\s*:\s*["']([A-Z][A-Z0-9_]{3,})["']"""),
    # 6+7) 异常构造调用。打包 BC 之前枚举器对 `detail={"code": ...}` 与
    #    StructuredError/`error_code` 之外的形状**完全失明**——补 `\w+Error(` 一次浮出
    #    27 条（渠道/适配器/服务层的构造调用，登记 FROZEN_FOR_TRIAGE）；而 BC 自己拆出的
    #    15 条 `UnsupportedSchema("OPENAPI_*", …)` 又瞒过了 `\w+Error(`（类名不以 Error 结尾），
    #    是同一个盲区的第二层，此处一并补上，否则"把一码多话变成机检"对这批码是空话。
    re.compile(r"""\w+(?:Error|Schema)\(\s*["']([A-Z][A-Z0-9_]{3,})["']"""),
    # 8) `code: str = "CODE"`——`code\s*=` 两种写法都漏掉带类型标注的默认值，
    #    是同一个盲区的第三层（`api/main.py` 的 `_credential_error` 默认方案码）。
    re.compile(r"""\bcode\s*:\s*[A-Za-z_]\w*\s*=\s*["']([A-Z][A-Z0-9_]{3,})["']"""),
)

# 打包 BD（docs/14 D55 第一批定性，2026-10-05）从 FROZEN_FOR_TRIAGE 移出的五条：只在
# 适配器内部被 catch 后折成 ActionResult.failed(StructuredError(...))，作为节点产出的
# result.code 出现；api 层（只有 main.py）零引用，没有任何端点把它们折成 HTTP 4xx/5xx
# 出体。故按 docs/57 §2.5「节点产出＝业务数据不译」归 TOOL_OUTPUT_ONLY。U1165 钉住这个
# 前提：一旦 api 层开始折算它们（出现出体路径），就得补 zh/en 译文而不是继续豁免。
TRIAGED_NODE_OUTPUT_ONLY = {
    "DB_NOT_CONFIGURED",  # database/adapter.py::_get_client，_execute catch
    "DB_SQL_ERROR",  # database/service.py，adapter._execute catch
    "DB_WRITE_FORBIDDEN",  # database/service.py 只读 fail-closed，adapter._execute catch
    "HTTP_CONNECT_ERROR",  # httpapi/service.py；httpapi 与 openapi 两适配器均内部 catch
    "HTTP_TIMEOUT",  # 同上
}

# 豁免表：每条都要写"为什么现在不能译"。加了新码而进这张表，等于把 A-4 重新欠一次。
TOOL_OUTPUT_ONLY = {
    # 只出现在工具/适配器结果的 error_code 里，UI 以机器码呈现（Monitoring 的 error_codes
    # 列、节点产出块）。docs/57 §2.5：节点产出＝业务数据，不译；译它等于把适配器日志塞进
    # i18n 目录。这一类是**长期口径**，不是待办（待办只有下面 COARSE_CODE 那一组）。
    "AUTH_FAILED",  # shop/adapter.py
    "BROWSER_NOT_STARTED",  # web/adapter.py
    # docs/08 打包 BC：由 `INVALID_PARAMETER` 拆出来的工具/适配器结果码——每条只承载一条
    # 答案，所以不再是"粗码"，但它们只出现在节点产出里（§2.5 业务数据不译），故进本表。
    "DATABASE_LIMIT_RANGE_INVALID",  # database/service.py
    "DATABASE_LIMIT_TYPE_INVALID",
    "DATABASE_PARAMS_INVALID",
    "HTTPAPI_BODY_INVALID",  # httpapi/service.py
    "HTTPAPI_HEADERS_INVALID",
    "HTTPAPI_METHOD_UNSUPPORTED",
    "HTTPAPI_TIMEOUT_INVALID",
    *TRIAGED_NODE_OUTPUT_ONLY,  # 打包 BD 从冻结桶定性移出的五条（见上）
    "MESSAGE_EMAIL_ADDRESS_INVALID",  # message/service.py
    "MESSAGE_FORMAT_INVALID",
    "MESSAGE_MENTIONS_INVALID",
    "MESSAGE_RECIPIENT_LIMIT",
    "MESSAGE_SECRET_CHANNEL_UNSUPPORTED",
    "MESSAGE_SECRET_TOO_LONG",
    "MESSAGE_SECRET_TYPE_INVALID",
    "MESSAGE_TO_INVALID",
    "TOOL_PARAMS_NOT_JSON",  # graph/loader.py
    "TOOL_PARAMS_NOT_OBJECT",
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
#: 2026-10-05 打包 BC：**这一组已被清空**——五条粗码拆成 **44** 条具体码（其中 29 条是契约
#: D-1 列举的，另 15 条是 `OPENAPI_INVALID_DOCUMENT` 借 `UnsupportedSchema` 一直瞒着的
#: `OPENAPI_REF_*`／`PARAM_*`／`BODY_*`／`KEYWORD_*`／`TYPE_*` 族——契约自己也没数全），
#: 再加机检一上就抓到的 `OPENAPI_DUPLICATE` 拆出的 2 条（`SPEC_ALREADY_IMPORTED`／
#: `RESTORE_CLASH`），合计 **46** 条（docs/08 打包 BC 收口）。
#: 且"一码多话"从此由 `test_u1160_no_code_carries_two_different_messages` 机检，不再靠手抄清单。
#: 留一个空集合是为了让 U1151 与 U1160 的并集写法不用改，也提醒下一位：这张表不该再被填回去。
COARSE_CODE: set[str] = set()
#: docs/08 打包 BC 量出的**枚举器盲区**：补上 `XxxError("CODE", …)` 形状后，27 条此前
#: 完全看不见的码浮出来（渠道/适配器/服务层构造调用）。它们**还没分类**——哪些走 HTTP
#: 响应要译、哪些只是节点产出按 §2.5 不译，得逐条读发射点才知道，塞进本批等于把 A-4
#: 重新欠一次。故单列一桶并登记 docs/14 **D55**，由 U1164 钉住它**只许缩小不许变大**：
#: 下批逐条分类（译或进该去的那一族），每分类一条就从这个冻结清单里删一条。
FROZEN_FOR_TRIAGE = {
    "CHANNEL_ALREADY_BOUND",
    "CHANNEL_ALREADY_REGISTERED",
    "CHANNEL_INVALID_RESPONSE",
    "CHANNEL_NOT_BOUND",
    "CHANNEL_UNAUTHORIZED",
    "CHANNEL_UPSTREAM_FAILED",
    "CONNECTION_NOT_FOUND",
    "IM_SEND_FAILED",
    "OAUTH_NO_REFRESH_TOKEN",
    "OAUTH_REFRESH_FAILED",
    "OAUTH_STATE_INVALID",
    "OAUTH_TOKEN_FAILED",
    "OPENAPI_INVALID_PARAMETER",
    "OPENAPI_NOT_SOFT_DELETED",
    "SMTP_SEND_FAILED",
    "WEBHOOK_MALFORMED",
    "WEBHOOK_SEND_FAILED",
}
#: 2026-10-05 打包 BC 量出的**原始 27 条**快照，刻意**硬编码**而非 `set(FROZEN_FOR_TRIAGE)`：
#: 若从当前集合派生，将来有人往桶里加新码会同时进快照，"只许缩小"的门永远绿、形同虚设。
#: U1164 断言 `FROZEN_FOR_TRIAGE <= FROZEN_SNAPSHOT`——每定性一条就从上面桶里删一条，
#: 快照本身（这份 27 条历史基线）不再改动。打包 BD 已移出 5 条纯节点产出码
#: （DB_NOT_CONFIGURED／DB_SQL_ERROR／DB_WRITE_FORBIDDEN／HTTP_CONNECT_ERROR／HTTP_TIMEOUT）。
FROZEN_SNAPSHOT = {
    "CHANNEL_ALREADY_BOUND",
    "CHANNEL_ALREADY_REGISTERED",
    "CHANNEL_INVALID_RESPONSE",
    "CHANNEL_NOT_BOUND",
    "CHANNEL_UNAUTHORIZED",
    "CHANNEL_UPSTREAM_FAILED",
    "CONNECTION_NOT_FOUND",
    "DB_NOT_CONFIGURED",
    "DB_SQL_ERROR",
    "DB_WRITE_FORBIDDEN",
    "DLQ_BODY_UNAVAILABLE",
    "DLQ_NOT_FAILED",
    "HTTP_CONNECT_ERROR",
    "HTTP_TIMEOUT",
    "IM_SEND_FAILED",
    "OAUTH_NO_REFRESH_TOKEN",
    "OAUTH_REFRESH_FAILED",
    "OAUTH_STATE_INVALID",
    "OAUTH_TOKEN_FAILED",
    "OPENAPI_FETCH_FAILED",
    "OPENAPI_INVALID_PARAMETER",
    "OPENAPI_LIMIT_EXCEEDED",
    "OPENAPI_NOT_SOFT_DELETED",
    "OPENAPI_UNSUPPORTED_VERSION",
    "SMTP_SEND_FAILED",
    "WEBHOOK_MALFORMED",
    "WEBHOOK_SEND_FAILED",
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
EXEMPT = TOOL_OUTPUT_ONLY | COARSE_CODE | DEAD_LETTER_REASON | FROZEN_FOR_TRIAGE


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


def test_u1151_allowlist_is_not_quietly_stale():
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
    assert emitted_codes('detail={"code": "OPENAPI_SPEC_ALREADY_IMPORTED", "message": "x"}') == {
        "OPENAPI_SPEC_ALREADY_IMPORTED"
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
    # 打包 BC：`OPENAPI_DUPLICATE` 拆成 SPEC_ALREADY_IMPORTED / RESTORE_CLASH 两条，
    # 这里跟着改成新的对照码（它同时是"目录成员判定"的正向对照）。
    assert "OPENAPI_SPEC_ALREADY_IMPORTED" in runtime
    # 前缀白名单时代这两类会被判成"不可译"，目录成员判定才把它们放进来。
    assert not re.match(r"^(COND_|WAIT_|LOOP_|FOREACH_|LLM_|RUNTIME_)", "SUBGRAPH_SUSPEND_UNSUPPORTED")


# --------------------------------------------------------------------------- U1151


@pytest.mark.parametrize("locale", ["zh-CN", "en-US"])
def test_u1151_allowlisted_codes_have_a_written_reason(locale: str) -> None:
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


# ---------------------------------------------------------- 打包 BC：U1160–U1164


#: 抓"码 ↔ 消息字面量"的配对：覆盖本仓实际在用的三种写法。
#: 1) detail={"code": "X", "message": "..."}（跨行也认）
#: 2) SomeError("X", "…")／StructuredError("X", "…")
#: 3) code="X" 后面跟着 message="…"（编译期 add/add_graph）
_PAIRS = (
    re.compile(
        r"""["']code["']\s*:\s*["'](?P<code>[A-Z][A-Z0-9_]{3,})["']\s*,\s*"""
        r"""["']message["']\s*:\s*(?P<msg>["'][^"']*["']|f["'][^"']*["'])""",
        re.S,
    ),
    re.compile(
        r"""\w+Error\(\s*["'](?P<code>[A-Z][A-Z0-9_]{3,})["']\s*,\s*"""
        r"""(?P<msg>["'][^"']*["']|f["'][^"']*["']|str\([^)]*\)|[A-Za-z_][\w.]*)""",
    ),
    re.compile(
        r"""code\s*=\s*["'](?P<code>[A-Z][A-Z0-9_]{3,})["']\s*,\s*"""
        r"""message\s*=\s*(?P<msg>["'][^"']*["']|f["'][^"']*["'])""",
        re.S,
    ),
)


def code_messages() -> dict[str, set[str]]:
    """码 → 它可以携带的**消息字面量**去重集合。

    只收"字面量"：`str(exc)`／变量名这类运行期才成形的消息收不进来，那部分由
    U1160 的注释照实说明——本判据是**保守的下界**，抓到的一定是多话，抓不到的仍需人看。
    """
    def normalize(msg: str) -> str:
        """把 f-string 的插值表达式抹平：`{duplicate.spec_id}：{duplicate.title}` 与
        `{duplicate[0]}：{duplicate[1]}` 是**同一句答案**，只是取值写法不同——不抹平会
        把"同一句话写在两处"误报成"一码多话"（打包 BC 拆 `OPENAPI_DUPLICATE` 时实测到）。
        """
        return re.sub(r"\{[^{}]*\}", "{}", msg)

    found: dict[str, set[str]] = {}
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for shape in _PAIRS:
            for m in shape.finditer(text):
                found.setdefault(m.group("code"), set()).add(normalize(m.group("msg")))
    return found


def test_u1160_no_code_carries_two_different_messages():
    """U1160：一码多话从"手抄清单"变成机检——同一码若带 ≥2 条不同消息字面量即红。

    这就是 docs/14 D53 ① 的正身：`INVALID_PARAMETER` 那五条之所以不能译，是因为按码
    出模板会把几条不同答案压成一句。拆完之后，**将来谁再写一条多话的码，这里会红**，
    而不是等某天有人发现英文态的诊断信息被压没了。
    """
    pairs = code_messages()
    bad = {
        code: sorted(msgs)
        for code, msgs in pairs.items()
        if len(msgs) > 1 and code not in EXEMPT
    }
    assert bad == {}, (
        f"这些码承载了多条不同消息，按码出文案会压掉诊断：{sorted(bad)}；"
        "拆成具体码（一条答案一个码），或给它补 params 让模板能区分"
    )


def test_u1161_the_coarse_codes_are_gone():
    """U1161：粗码在全仓零发射（拆码没拆一半）。

    BC 拆了五条；打包 BF 再拆两条 OpenAPI 粗码（fetch／limit 各承载两个答案）。
    """
    where = structured_codes()
    coarse = (
        "INVALID_PARAMETER", "NOT_FOUND",
        "OPENAPI_INVALID_CREDENTIAL", "OPENAPI_INVALID_DOCUMENT",
        "WAIT_EVENT_PAYLOAD_INVALID",
        # 打包 BF（docs/08）：拆成 FETCH_NETWORK/FETCH_HTTP 与 SPEC_OPERATIONS/TENANT_SPECS。
        "OPENAPI_FETCH_FAILED", "OPENAPI_LIMIT_EXCEEDED",
    )
    leftovers = {c: where[c] for c in coarse if c in where}
    assert leftovers == {}, f"旧粗码仍在发射：{leftovers}"


def test_u1162_http_facing_new_codes_have_both_locales():
    """U1162：会进 HTTP 422/404 响应的新码，zh/en 双份齐全（否则英文态回退中文原文）。"""
    auth, runtime, validation = _catalog_keys()
    zh = set(json.loads((LOCALES / "zh-CN" / "runtime.json").read_text(encoding="utf-8")))
    en = set(json.loads((LOCALES / "en-US" / "runtime.json").read_text(encoding="utf-8")))
    http_facing = {
        "DELIVERY_NOT_FOUND", "DELIVERY_STATUS_INVALID", "CONNECTION_SCOPES_INVALID",
        # 打包 BE（docs/14 D55 第二批）：从冻结桶定性为"经真实端点结构化出体"的码，
        # 补 zh/en 后纳入 HTTP 面双份校验。
        "DLQ_BODY_UNAVAILABLE", "DLQ_NOT_FAILED",  # POST /api/demo/deliveries/{seq}/replay
        "OPENAPI_UNSUPPORTED_VERSION",  # parser → _openapi_http_error → 422
        # 打包 BF（docs/08）：两条一码多话粗码拆出的四个具体码，均经端点结构化出体。
        "OPENAPI_FETCH_NETWORK_ERROR", "OPENAPI_FETCH_HTTP_ERROR",  # preview URL 抓取
        "OPENAPI_SPEC_OPERATIONS_LIMIT", "OPENAPI_TENANT_SPECS_LIMIT",  # import 配额
        "OPENAPI_BODY_MISSING_SCHEMA", "OPENAPI_BODY_NOT_JSON",
        "OPENAPI_CREDENTIAL_BASIC_INCOMPLETE", "OPENAPI_CREDENTIAL_SCHEME_UNKNOWN",
        "OPENAPI_DOCUMENT_MISSING_SERVERS", "OPENAPI_DOCUMENT_NOT_OPENAPI3",
        "OPENAPI_DOCUMENT_SERVER_URL_NOT_ABSOLUTE", "OPENAPI_DOCUMENT_SOURCE_EXCLUSIVE",
        "OPENAPI_PARAM_MISSING_NAME_IN", "OPENAPI_PARAM_MISSING_SCHEMA",
        "OPENAPI_PARAM_NOT_OBJECT", "OPENAPI_PARAM_REF_NOT_OBJECT",
        "WAIT_EVENT_PAYLOAD_NOT_OBJECT", "WAIT_EVENT_PAYLOAD_TOO_LARGE",
        "WAIT_EVENT_PAYLOAD_TOO_MANY_KEYS",
        "OPENAPI_SPEC_ALREADY_IMPORTED", "OPENAPI_RESTORE_CLASH",
        # schema.py 的解析期码（经 `_openapi_http_error` 落到 422，所以要译）
        "OPENAPI_KEYWORD_ONEOF", "OPENAPI_KEYWORD_UNSUPPORTED", "OPENAPI_REF_CYCLE",
        "OPENAPI_REF_EXTERNAL", "OPENAPI_REF_MISSING", "OPENAPI_REF_TOO_DEEP",
        "OPENAPI_SCHEMA_NOT_OBJECT", "OPENAPI_TYPE_NULL_ONLY", "OPENAPI_TYPE_UNSUPPORTED",
    }
    missing = sorted(c for c in http_facing if c not in zh or c not in en)
    assert missing == [], f"这些 HTTP 面的新码缺 zh 或 en 文案：{missing}"
    # 它们必须真的被目录认出来（isRuntimeErrorCode 是"目录成员"判定，不看前缀）
    assert http_facing <= (auth | runtime | validation)
    # 每条只允许一条答案：这些码在源码里不得出现 ≥2 条消息字面量
    pairs = code_messages()
    multi = {c: sorted(pairs[c]) for c in http_facing if len(pairs.get(c, ())) > 1}
    assert multi == {}, f"新码自己又变成多话了：{multi}"


def test_u1163_reverse_gate_the_multi_message_detector_is_real():
    """U1163 反向门：合成片段里给同一码配两条不同消息，U1160 的判据必须点名。"""
    snippet = (
        'raise HTTPException(status_code=422, detail={"code": "BC_PLANTED", '
        '"message": "第一条"})\n'
        'raise HTTPException(status_code=422, detail={"code": "BC_PLANTED", '
        '"message": "第二条"})\n'
    )
    seen: dict[str, set[str]] = {}
    for shape in _PAIRS:
        for m in shape.finditer(snippet):
            seen.setdefault(m.group("code"), set()).add(m.group("msg"))
    assert len(seen.get("BC_PLANTED", ())) == 2, "枚举器对同一码的多条消息失明＝守护是假的"
    assert "BC_PLANTED" not in EXEMPT, "植进来的码不该在豁免表上，否则点名不到"


def test_u1164_frozen_triage_bucket_only_shrinks():
    """U1164：待分类桶是**冻结快照**，只许缩小（每分类一条删一条），不许变大。

    没有这条，"未分类"就会变成第二个垃圾桶：下一个人加码时顺手往里一放就绿了。
    """
    extra = sorted(FROZEN_FOR_TRIAGE - FROZEN_SNAPSHOT)
    assert extra == [], (
        f"待分类桶变大了：{extra}；新码必须当场判明是『走 HTTP 要译』还是『只出现在节点产出』，"
        "不许挂进这里——分类旧的那些请从桶里删掉"
    )


def test_u1165_triaged_node_output_codes_never_reach_an_http_response():
    """U1165（打包 BD，docs/14 D55 定性前提）：TRIAGED_NODE_OUTPUT_ONLY 这五条被判为
    「仅节点产出、不译」，前提是 **api 层零出体路径**——没有任何端点把它们折算成 HTTP
    4xx/5xx 响应。若哪天有人在 api/ 引用/下发这些码（它们因此到得了英文使用者），
    这条守护必须变红：那时就该补 zh/en 译文、移出 TOOL_OUTPUT_ONLY，而不是继续豁免。

    与 U1151（豁免表不得腐烂）互补：U1151 只验"码还发得出来"，验不了"它从哪条路出去"。
    """
    api_dir = SRC / "api"
    leaked: dict[str, list[str]] = {}
    for path in sorted(api_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for code in TRIAGED_NODE_OUTPUT_ONLY:
            if re.search(rf"\b{re.escape(code)}\b", text):
                leaked.setdefault(code, []).append(str(path.relative_to(REPO)))
    assert leaked == {}, (
        "这些码被归为『仅节点产出不译』，却在 api 层出体路径上出现了："
        f"{leaked}；既然到得了 HTTP 使用者，就补 zh/en 译文并移出 TRIAGED_NODE_OUTPUT_ONLY。"
    )
