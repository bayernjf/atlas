"""Prometheus text exposition format 导出（零新依赖，docs/34 §五 P1）。

输入为各租户 monitoring.snapshot_metrics() 的快照（形状见
monitoring.metrics.summarize），输出 Prometheus 0.0.4 文本：

    # HELP atlas_runs_total ...
    # TYPE atlas_runs_total gauge
    atlas_runs_total{tenant_id="demo",status="healthy"} 12

只暴露聚合计数与分位数，不含图名、节点错误文本、变量值等业务细节；
tenant_id 为内部标识。正式 OTel/Prometheus 栈缓做 docs/14 D11。
"""

from __future__ import annotations

import math
from typing import Any, Iterable

# 指标名 → (HELP 文案, 类型)
_HEADERS: dict[str, tuple[str, str]] = {
    "atlas_up": ("Atlas 进程存活标记（恒为 1）", "gauge"),
    "atlas_storage_backend_info": ("存储后端信息（按 backend 标签取值恒为 1）", "gauge"),
    "atlas_tenants_active": ("进程内已装配租户数", "gauge"),
    "atlas_runs_total": ("监控环形窗口内的运行计数（按健康状态）", "gauge"),
    "atlas_runs_success_rate": ("运行成功率（0-1，窗口内无样本时无此序列）", "gauge"),
    "atlas_run_duration_ms": ("运行耗时分位数（毫秒）", "gauge"),
    "atlas_tool_calls_total": ("工具调用计数（按调用结果）", "gauge"),
}


def _escape_label(value: str) -> str:
    """转义 label 值里的反斜杠、双引号、换行（Prometheus 规范）。"""
    return (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", "\\n")
    )


def _labels(**pairs: str) -> str:
    if not pairs:
        return ""
    body = ",".join(f'{key}="{_escape_label(str(val))}"' for key, val in pairs.items())
    return "{" + body + "}"


def _num(value: Any) -> str | None:
    """把快照数值格式化为 Prometheus 数值；None/非有限数返回 None（跳过该序列）。"""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    # 整数不带小数点，浮点保留紧凑表示
    return str(int(number)) if number.is_integer() else f"{number:.6g}"


def render_prometheus(
    *,
    storage_backend: str,
    tenant_snapshots: Iterable[tuple[str, dict[str, Any]]],
) -> str:
    """把进程内监控快照渲染为 Prometheus 文本。

    tenant_snapshots: (tenant_id, snapshot_metrics() 返回值) 可迭代对象。
    """
    lines: list[str] = []
    emitted: set[str] = set()

    def emit(name: str, value: str, labels: dict[str, str] | None = None) -> None:
        if name not in emitted:
            help_text, mtype = _HEADERS[name]
            lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} {mtype}")
            emitted.add(name)
        suffix = _labels(**(labels or {}))
        lines.append(f"{name}{suffix} {value}")

    # 进程级
    emit("atlas_up", "1")
    emit("atlas_storage_backend_info", "1", {"backend": storage_backend})

    snapshots = list(tenant_snapshots)
    emit("atlas_tenants_active", str(len(snapshots)))

    for tenant_id, snap in snapshots:
        base = {"tenant_id": tenant_id}

        total = _num(snap.get("total"))
        healthy = _num(snap.get("healthy"))
        unhealthy = _num(snap.get("unhealthy"))
        if total is not None:
            if healthy is not None:
                emit("atlas_runs_total", healthy, {**base, "status": "healthy"})
            if unhealthy is not None:
                emit("atlas_runs_total", unhealthy, {**base, "status": "unhealthy"})

        rate = _num(snap.get("success_rate"))
        if rate is not None:
            emit("atlas_runs_success_rate", rate, base)

        for quantile, key in (("0.5", "p50"), ("0.95", "p95")):
            value = _num(snap.get(key))
            if value is not None:
                emit("atlas_run_duration_ms", value, {**base, "quantile": quantile})

        for tool in snap.get("tools", []) or []:
            tool_name = str(tool.get("tool", "unknown"))
            calls = tool.get("calls", 0)
            simulated = tool.get("simulated", 0)
            failed = tool.get("failed", 0)
            # calls = 真实成功 + failed + simulated；成功数由差值给出
            success = max(int(calls) - int(simulated) - int(failed), 0)
            emit("atlas_tool_calls_total", _num(success) or "0",
                 {**base, "tool": tool_name, "status": "success"})
            emit("atlas_tool_calls_total", _num(failed) or "0",
                 {**base, "tool": tool_name, "status": "failed"})
            emit("atlas_tool_calls_total", _num(simulated) or "0",
                 {**base, "tool": tool_name, "status": "simulated"})

    return "\n".join(lines) + "\n"
