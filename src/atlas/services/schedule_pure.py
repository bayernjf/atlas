"""Pure scheduling helpers extracted from api/main.py (2026-11-02 relay).

Kept free of module-level state (tenant_registry, worker pool, store
singletons): each function derives its output from env/config or a plain
graph payload only, so the scheduler block of main.py can stay testable
without importing the API app.
"""

import logging
import os

logger = logging.getLogger(__name__)

MIN_SCHEDULE_TICK_SECONDS = 1
MAX_SCHEDULE_TICK_SECONDS = 300


def schedule_tick_seconds() -> int:
    raw = os.getenv("ATLAS_SCHEDULE_TICK_SECONDS", "").strip()
    if not raw:
        return 30
    try:
        value = int(raw)
    except ValueError:
        logger.warning("ATLAS_SCHEDULE_TICK_SECONDS 非整数（%r），按默认 30 秒", raw)
        return 30
    bounded = max(MIN_SCHEDULE_TICK_SECONDS, min(MAX_SCHEDULE_TICK_SECONDS, value))
    if bounded != value:
        logger.warning(
            "ATLAS_SCHEDULE_TICK_SECONDS %s 超出 %s-%s，按 %s 秒",
            raw, MIN_SCHEDULE_TICK_SECONDS, MAX_SCHEDULE_TICK_SECONDS, bounded,
        )
    return bounded


def schedule_enabled() -> bool:
    return os.getenv("ATLAS_SCHEDULE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def schedule_specs_of_published(
    graph_raw: dict,
) -> list[tuple[str, str, str, int, str]]:
    """已发布版本里**全部**定时触发节点的调度形状（打包 ZN，D41 ③；发布派生）。

    返回每条 `(schedule_id, cron, timezone, catch_up_minutes, overlap_policy)`——
    schedule_id 即该定时触发**节点 id**（随图钉版、稳定），一图挂 N 个定时节点就返回 N 条。
    节点 id 缺失或以保留前缀 `__` 开头（撞 reflect／存量回填命名空间）时日志点名跳过：
    静默建一条会与保留行主键冲突，阻断发布又把极端命名问题变成发布事故，二者都不取。
    """
    found: list[tuple[str, str, str, int, str]] = []
    for node in graph_raw.get("nodes") or []:
        if not isinstance(node, dict) or node.get("type") != "trigger":
            continue
        config = node.get("config") or {}
        if config.get("triggerType") in ("schedule", "cron") and config.get("cron"):
            node_id = str(node.get("id", ""))
            if not node_id or node_id.startswith("__"):
                logger.warning(
                    "定时触发节点 id 缺失或撞保留命名空间，跳过该调度：%r", node_id
                )
                continue
            catch_up = config.get("catchUpMinutes", 0)
            try:
                catch_up = max(0, int(catch_up))
            except (TypeError, ValueError):
                catch_up = 0
            overlap = config.get("overlapPolicy", "skip")
            overlap = overlap if overlap in ("skip", "allow") else "skip"
            found.append((
                node_id, str(config["cron"]),
                str(config.get("timezone", "UTC")), catch_up, overlap,
            ))
    return found


def no_schedule_detail(graph_id: str, schedule_id: str | None = None) -> str:
    label = f"（schedule={schedule_id}）" if schedule_id else ""
    return (
        f"图 {graph_id} 没有定时调度{label}：只有含定时触发节点的**已发布**版本才会登记"
    )
