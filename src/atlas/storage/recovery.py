"""中断恢复（M5b 批 2-2，docs/24 §2.4 / 08 M5b 立项条）。

`make_frame_sink` 把挂起帧写入 `interruptions` 表（PG 后端）；`load_pending_frames`
读回未决帧，供服务启动钩子逐帧重建 pending + 重启续跑线程。恢复幂等：帧以
`resume_token` 为主键，重复启动不重复建帧；失败帧由调用方隔离记日志、不阻塞其余。
"""

from __future__ import annotations

import json
from typing import Any, Callable

from sqlalchemy import Engine, text


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

    返回的每帧为完整帧 dict（payload 反序列化）+ 顶部附加 tenant_id/run_id。
    """
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT resume_token, tenant_id, run_id, node_id, kind, payload "
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
        frames.append(frame)
    return frames


def clear_frame(engine: Engine, token: str) -> None:
    """决策送达后清除挂起帧（幂等；恢复扫描器亦用其清理已续跑完的帧）。"""
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM interruptions WHERE resume_token = :token"),
            {"token": token},
        )
