"""中断恢复（M5b 批 2-2，docs/24 §2.4 / 08 M5b 立项条）。

`make_frame_sink` 把挂起帧写入 `interruptions` 表（PG 后端）；`load_pending_frames`
读回未决帧，供服务启动钩子逐帧重建 pending + 重启续跑线程。恢复幂等：帧以
`resume_token` 为主键，重复启动不重复建帧；失败帧由调用方隔离记日志、不阻塞其余。

`claim_frame_for_resume`（docs/62 §3.2 L2）是**唯一**的"谁可以越过挂起点继续执行下游"裁定：
一条 `UPDATE ... WHERE resumed_at IS NULL` 的 rowcount 即互斥本身，不引 advisory lock、
不引 `SELECT FOR UPDATE SKIP LOCKED`、不引定时器。时钟只取 DB 的 `CURRENT_TIMESTAMP`。
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import Engine, text

from atlas.scheduling.models import to_utc_iso


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()

#: 进程身份（仅落 `resumed_by` 供排障，不参与判定，不做注册中心）。
PROCESS_IDENTITY = f"{socket.gethostname()}:{os.getpid()}"


def claim_frame_for_resume(engine: Engine, resume_token: str, who: str = PROCESS_IDENTITY) -> bool:
    """原子一次性认领：只有把 `resumed_at` 从 NULL 翻成 DB 当前时刻的那个进程返回 True。

    True ＝ 本进程独占越过该挂起点、继续执行下游的权利；False ＝ 已被别的进程消费，
    调用方**必须停止驱动**（不执行后续节点、不写 run 终态、不清帧）。不抛：认领失败是
    跨进程竞态的正常结果，不是异常（fail-safe 家族同 `_record_history`/`_safe_record`）。
    """
    try:
        with engine.begin() as conn:
            result = conn.execute(
                text(
                    "UPDATE interruptions SET resumed_at = CURRENT_TIMESTAMP, resumed_by = :who "
                    "WHERE resume_token = :token AND resumed_at IS NULL"
                ),
                {"who": who, "token": resume_token},
            )
        return (result.rowcount or 0) == 1
    except Exception:  # noqa: BLE001 - 库不可达时按"未认领"处理，宁可停止驱动也不重复执行
        return False


def make_resume_claim(engine: Engine) -> Callable[[str], bool]:
    """给 loader 的 `resume_claim` 注入闭包（与 `frame_sink` 同一注入口风格，docs/62 §2 D-2）。"""
    who = PROCESS_IDENTITY

    def claim(token: str) -> bool:
        return claim_frame_for_resume(engine, token, who)

    return claim


def make_frame_sink(engine: Engine, tenant_id: str, run_id: str) -> Callable[[dict], None]:
    """返回写 `interruptions` 帧的 frame_sink（PG 后端；进程内后端不注入）。

    完整帧（含 graph_snapshot/resume_state/summary/approver/deadline_at）序列化进
    payload jsonb；`ON CONFLICT` 保证同一 token 重复挂起只保留最新帧（幂等）。
    """

    def sink(frame: dict) -> None:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO interruptions "
                    "(resume_token, tenant_id, run_id, node_id, kind, payload, deadline_at, created_at) "
                    "VALUES (:token, :tenant_id, :run_id, :node_id, :kind, :payload, :deadline_at, "
                    "CURRENT_TIMESTAMP) "
                    "ON CONFLICT (resume_token) DO UPDATE SET payload = EXCLUDED.payload"
                ),
                {
                    "token": frame["resume_token"],
                    "tenant_id": tenant_id,
                    "run_id": run_id,
                    "node_id": frame["node_id"],
                    "kind": frame["kind"],
                    "payload": json.dumps(frame, ensure_ascii=False),
                    "deadline_at": frame.get("deadline_at"),
                },
            )

    return sink


def _frame_from_row(row: tuple) -> dict:
    """帧行 → 投影 dict（payload 反序列化 ＋ 顶部附列值）。

    `resumed_at` 是**列值而非 payload 值**（docs/62 §3.1）：NULL＝还没人越过这个挂起点，
    非空＝已被某进程认领消费。
    """
    payload = row[5]
    # psycopg 3 读 jsonb 已解析为 dict；若驱动返回字符串则兜底反序列化。
    frame = json.loads(payload) if isinstance(payload, str) else payload
    frame["resume_token"] = row[0]
    frame["tenant_id"] = row[1]
    frame["run_id"] = row[2]
    frame["node_id"] = row[3]
    frame["kind"] = row[4]
    frame["resumed_at"] = row[6]
    frame["resumed_by"] = row[7]
    return frame


_FRAME_COLUMNS = (
    "SELECT resume_token, tenant_id, run_id, node_id, kind, payload, "
    "resumed_at, resumed_by "
    "FROM interruptions "
)


def load_pending_frames(engine: Engine) -> list[dict]:
    """读**全部**未决挂起帧（跨租户），按 created_at 升序。

    跨租户是启动恢复的正确语义（一个进程替所有租户恢复），别把它当通用查询用——
    HTTP 侧要租户内视图请用 `list_tenant_frames`（docs/76 §1 D-2）。本函数不过滤已认领
    帧：恢复扫描器据此跳过，排障/测试则要看全量。
    """
    with engine.connect() as conn:
        rows = conn.execute(text(_FRAME_COLUMNS + "ORDER BY created_at")).all()
    return [_frame_from_row(row) for row in rows]


def list_tenant_frames(engine: Engine, tenant_id: str) -> list[dict]:
    """读**本租户**挂起帧（含已认领的），按 created_at 升序；走 `idx_interruptions_tenant`。

    只读。与 `load_pending_frames` 分两条路是刻意的：把全局扫描拿来做 HTTP 端点，等于
    先把别租户的 resume_token 读进本进程内存再在 Python 里丢掉，还放弃了租户索引
    （docs/76 §1 D-2）。
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(_FRAME_COLUMNS + "WHERE tenant_id = :tenant_id ORDER BY created_at"),
            {"tenant_id": tenant_id},
        ).all()
    return [_frame_from_row(row) for row in rows]


def clear_frame(engine: Engine, token: str) -> None:
    """决策送达后清除挂起帧（幂等；恢复扫描器亦用其清理已续跑完的帧）。"""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM interruptions WHERE resume_token = :token"),
            {"token": token},
        )


#: 人工收敛（docs/96 打包 BL D-4）的事务结果码。
RESOLVE_OK = "ok"
RESOLVE_FRAME_NOT_FOUND = "frame_not_found"
RESOLVE_FRAME_NOT_CLAIMED = "frame_not_claimed"
RESOLVE_RUN_NOT_SUSPENDED = "run_not_suspended"


def abandon_claimed_suspended_frame(
    engine: Engine, *, tenant_id: str, token: str
) -> tuple[str, dict]:
    """把「已认领、run 仍 suspended」的卡死帧人工了结：run 置 interrupted、删帧。

    docs/96 打包 BL（docs/14 D36 人工收敛半边）。**承重的是这一个事务的谓词**，
    调用方的只读预查只为折算给人看的错误码，不承担互斥：

    - 帧行带 `resumed_at IS NOT NULL`（已认领）谓词才删——未认领帧重启恢复扫描器
      仍会驱动它，人工了结会抢跑，绝不删；
    - run 带 `status = 'suspended'` 谓词才置终态——running（可能只是续跑进行中的
      瞬时态）或已终态都不动，也不静默删帧（frame_lingering 说明另有 bug，只观测）。

    承重的是 UPDATE/DELETE 的 WHERE 谓词，调用方的只读预查只用于折算给人看的
    错误码、不承担互斥。两步同一事务提交，任一谓词不命中显式回滚（不依赖
    "with begin 出异常才回滚"的隐式行为）。返回 ``(code, info)``：``info`` 在
    ok 时为 ``{"run_id","kind"}``，在 run_not_suspended 时带 ``{"status"}``。
    """
    with engine.begin() as conn:
        # 承重 UPDATE：CTE 先把「本租户 + 本 token + 已认领」的帧行锁出来，
        # 只更新其名下且恰为 suspended 的 run。谓词不放行 ⇒ rowcount 0。
        result = conn.execute(
            text(
                "WITH target AS ("
                "  SELECT run_id FROM interruptions "
                "  WHERE resume_token = :token AND tenant_id = :tenant_id "
                "  AND resumed_at IS NOT NULL"
                "  FOR UPDATE"
                ") "
                "UPDATE runs SET status = 'interrupted', finished_at = :now, "
                "kind = NULL, node_id = NULL, deadline_at = NULL, resume_token = NULL "
                "WHERE id = (SELECT run_id FROM target) "
                "AND tenant_id = :tenant_id AND status = 'suspended' "
                "RETURNING id"
            ),
            {"now": _now_iso(), "token": token, "tenant_id": tenant_id},
        ).first()
        if result is None:
            # 承重谓词没放行（UPDATE 0 行、本事务零写入，无需回滚）：只读折算
            # 具体码（帧不存在 / 未认领 / run 非 suspended），折算不承担互斥。
            row = conn.execute(
                text(
                    "SELECT run_id FROM interruptions "
                    "WHERE resume_token = :token AND tenant_id = :tenant_id"
                ),
                {"token": token, "tenant_id": tenant_id},
            ).first()
            if row is None:
                return RESOLVE_FRAME_NOT_FOUND, {}
            run_id = row[0]
            claimed = conn.execute(
                text(
                    "SELECT 1 FROM interruptions "
                    "WHERE resume_token = :token AND tenant_id = :tenant_id "
                    "AND resumed_at IS NOT NULL"
                ),
                {"token": token, "tenant_id": tenant_id},
            ).first()
            if claimed is None:
                return RESOLVE_FRAME_NOT_CLAIMED, {}
            current = conn.execute(
                text("SELECT status FROM runs WHERE id = :run_id AND tenant_id = :tenant_id"),
                {"run_id": run_id, "tenant_id": tenant_id},
            ).first()
            return RESOLVE_RUN_NOT_SUSPENDED, {"status": current[0] if current else None}

        deleted = conn.execute(
            text(
                "DELETE FROM interruptions "
                "WHERE resume_token = :token AND tenant_id = :tenant_id "
                "AND resumed_at IS NOT NULL"
            ),
            {"token": token, "tenant_id": tenant_id},
        )
        if (deleted.rowcount or 0) != 1:
            # CTE 已锁到帧行、同事务内不可能消失；走到这里是真不一致，让 begin
            # 回滚（run 的 UPDATE 一并撤销），交调用方按 500 看见而不是静默放过。
            raise RuntimeError(f"abandon frame deleted {deleted.rowcount} rows: {token}")
        # 承重谓词已放行：run_id 由 RETURNING 取回，帧随后随本事务删除。
        return RESOLVE_OK, {"run_id": result[0], "kind": None}


def clear_tenant_frames(engine: Engine, tenant_id: str) -> None:
    """/api/demo/reset 的 PG 档分层：清本租户挂起帧（recordings/feedback 保留）。"""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM interruptions WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )


# --- 挂起帧的只读投影（docs/76 打包 Q；REST 与 MCP 共用一份） --------------------
#
# 两个只读面都看同一张表：`GET /api/interruptions` 与 MCP 的 `atlas_list_interruptions`
# 只差「是否带 resumeToken」（docs/91 §3 的刻意收窄），其余逐键一致由本函数保证。

_TERMINAL_RUN_STATES = {"completed", "failed", "cancelled", "interrupted"}


def interruption_state(claimed: bool, run_status: str | None) -> str:
    """帧列值＋run 状态 → 描述性档位（docs/76 §1 D-3：**只描述，不判决、不计时**）。"""
    if not claimed:
        return "awaiting"
    if run_status is None:
        return "claimed_unknown_run"
    if run_status == "running":
        return "claimed_executing"
    if run_status == "suspended":
        return "claimed_suspended"
    if run_status in _TERMINAL_RUN_STATES:
        return "frame_lingering"
    return "claimed_other_state"


def claimed_seconds(claimed_at: object) -> int | None:
    """已认领多久；算不出来就回 None（不拿 0 冒充"刚认领"）。"""
    if isinstance(claimed_at, str):
        try:
            claimed_at = datetime.fromisoformat(claimed_at.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(claimed_at, datetime):
        return None
    moment = claimed_at if claimed_at.tzinfo else claimed_at.replace(tzinfo=timezone.utc)
    return max(0, int((datetime.now(timezone.utc) - moment.astimezone(timezone.utc)).total_seconds()))


def interruption_view(
    *,
    backend: str,
    tenant_id: str,
    engine: Engine | None,
    run_status_of: Callable[[str], str | None],
    include_resume_token: bool,
) -> dict:
    """本租户挂起帧的只读投影（docs/76 §1；**不重放、不清帧、不改任何状态**）。

    `include_resume_token=False` 是 MCP 面的口径：对外面不携带可用于续跑的令牌
    （docs/89 A-8 已登记该字段随投影外泄的风险）。REST 面仍为 True——收窄是本面行为，
    不是全局改动。内存档根本不写帧表，故"空"不等于"没有卡住的 run"，显式自报
    `visibility="frames-not-persisted"`。
    """
    if backend != "pg" or engine is None:
        return {"backend": backend, "visibility": "frames-not-persisted", "items": []}
    items: list[dict] = []
    for frame in list_tenant_frames(engine, tenant_id):
        claimed_at = frame.get("resumed_at")
        run_id = frame.get("run_id") or ""
        run_status = run_status_of(run_id) if run_id else None
        item: dict = {
            "runId": run_id,
            "graphId": (frame.get("resume_state") or {}).get("graph_id", ""),
            "nodeId": frame.get("node_id", ""),
            "kind": frame.get("kind", ""),
            "claimedAt": to_utc_iso(claimed_at) if claimed_at else None,
            "claimedBy": frame.get("resumed_by"),
            "claimedSeconds": claimed_seconds(claimed_at) if claimed_at else None,
            "deadlineAt": frame.get("deadline_at"),
            "runStatus": run_status,
            "state": interruption_state(bool(claimed_at), run_status),
        }
        if include_resume_token:
            item["resumeToken"] = frame.get("resume_token", "")
        items.append(item)
    return {"backend": backend, "visibility": "tenant-scoped", "items": items}
