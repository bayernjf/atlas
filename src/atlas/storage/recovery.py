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
from typing import Callable

from sqlalchemy import Engine, text

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


def load_pending_frames(engine: Engine) -> list[dict]:
    """读全部未决挂起帧（含 tenant_id/run_id），按 created_at 升序。

    返回的每帧为完整帧 dict（payload 反序列化）+ 顶部附加 tenant_id/run_id/resumed_at/resumed_by。
    `resumed_at` 是**列值而非 payload 值**（docs/62 §3.1）：NULL＝还没人越过这个挂起点，
    非空＝已被某进程认领消费。本函数不过滤已认领帧——调用方按各自语义决定：
    恢复扫描器据此跳过（不再为已消费的帧起线程），排障/测试则要看全量。
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT resume_token, tenant_id, run_id, node_id, kind, payload, "
                "resumed_at, resumed_by "
                "FROM interruptions ORDER BY created_at"
            )
        ).all()
    frames: list[dict] = []
    for row in rows:
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
        frames.append(frame)
    return frames


def clear_frame(engine: Engine, token: str) -> None:
    """决策送达后清除挂起帧（幂等；恢复扫描器亦用其清理已续跑完的帧）。"""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM interruptions WHERE resume_token = :token"),
            {"token": token},
        )


def clear_tenant_frames(engine: Engine, tenant_id: str) -> None:
    """/api/demo/reset 的 PG 档分层：清本租户挂起帧（recordings/feedback 保留）。"""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM interruptions WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
