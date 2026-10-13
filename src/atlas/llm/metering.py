"""LLM 用量计量（docs/129 打包 AU＝D70；AU-D1…D5 的形状权威）。

六个 `litellm.completion` 调用点此前各自丢回包的 usage。本模块是**唯一**封装
`litellm.completion` 的地方（AU-D3「一件事一个实现」——打包 AE 脱敏纪律延伸到
用量面，禁第二份），并承担三件事：

1. **usage 提取**：从 ModelResponse 取 prompt/completion/total tokens（供应商不返
   ⇒ 记 0 且 warning，记录不丢——调用发生过是事实，AU-D5／U1416）；
2. **归属**：contextvar `usage_context` 携带 tenant/run/graph/node＋store 引用
   （AU-D4）——不改六个消费方 Protocol 签名；**无上下文 ⇒ 不记录**（loader 直跑
   与单测不计，契约明写）；后台线程（SSE worker／调度）在本线程内显式 set，
   不依赖跨线程继承（tracing 同款纪律，06 §6.12）；
3. **记录**：成功与失败（异常路径）都落一条；失败＝零 token＋`status="error"`
   （AU-D5——"花了时间没拿到 token"本身就是成本信号）。

**旁路性**：记录自身任何失败只 warning，绝不影响调用方行为与运行终态
（U1425 反向门守这条）。**不记正文**：无 prompt/response 字段（隐私与体积）。
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Iterator, Literal, Protocol

logger = logging.getLogger(__name__)

#: 内存档 ring 容量（AU-D2：照 monitoring ring 200／reflection ring 100 先例放大；
#: 超出覆盖最旧，不静默扩容——成本账的权威档是 PG，内存档只是 Demo/无 PG 形态）。
USAGE_RING_SIZE = 2000

#: 八个调用点身份（docs/129 §3.3 表；新增调用点必须在此登记，禁裸字符串）。
CallerKind = Literal[
    "ai_decision",
    "condition_llm",
    "intent_recognition",
    "info_extraction",
    "content_generation",
    "memory_auto_extract",
    "nl_generate",
    "reflection_summarize",
]

UsageStatus = Literal["ok", "error"]

GroupBy = Literal["caller", "model", "day"]


@dataclass
class UsageRecord:
    """一次 LLM 调用的计量记录；字段与迁移 049 `llm_usage` 列一一对应。"""

    tenant_id: str
    ts: str  # ISO8601 UTC（与 runs.started_at 同族）
    caller: str
    model: str  # 实际发出的模型名（含节点 model 覆盖后的）
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    status: str = "ok"
    node_id: str = ""
    graph_id: str = ""
    run_id: str = ""

    def public_view(self) -> dict:
        """REST 投影（camelCase，与前端消费点对齐；零新增脱敏面——本无敏感字段）。"""
        return {
            "tenantId": self.tenant_id,
            "ts": self.ts,
            "caller": self.caller,
            "model": self.model,
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "totalTokens": self.total_tokens,
            "latencyMs": round(self.latency_ms, 1),
            "status": self.status,
            "nodeId": self.node_id,
            "graphId": self.graph_id,
            "runId": self.run_id,
        }


class UsageStore(Protocol):
    """两档 store 公共接缝（内存/PG 实现互换，方法签名一比一）。"""

    def record(self, rec: UsageRecord) -> None: ...

    def list_recent(self, limit: int = 50) -> list[dict]: ...

    def summarize(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        group_by: str = "caller",
    ) -> list[dict]: ...

    def reset(self) -> None: ...


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _in_window(ts: str, since: str | None, until: str | None) -> bool:
    """ISO 字典序比较（TEXT 列同族，docs/61 H3 先例）；非法输入不参与过滤。"""
    if since and ts < since:
        return False
    if until and ts > until:
        return False
    return True


def _group_key(rec: UsageRecord, group_by: str) -> str:
    if group_by == "model":
        return rec.model
    if group_by == "day":
        return rec.ts[:10]
    return rec.caller


def _aggregate(records: list[UsageRecord], group_by: str) -> list[dict]:
    """分组聚合（内存档与 PG 档 SQL 口径必须一致：调用次数＋三项 token 合计）。"""
    buckets: dict[str, dict[str, Any]] = {}
    for rec in records:
        key = _group_key(rec, group_by)
        bucket = buckets.setdefault(
            key,
            {
                "key": key,
                "calls": 0,
                "promptTokens": 0,
                "completionTokens": 0,
                "totalTokens": 0,
                "errors": 0,
            },
        )
        bucket["calls"] += 1
        bucket["promptTokens"] += rec.prompt_tokens
        bucket["completionTokens"] += rec.completion_tokens
        bucket["totalTokens"] += rec.total_tokens
        if rec.status == "error":
            bucket["errors"] += 1
    return sorted(buckets.values(), key=lambda item: item["totalTokens"], reverse=True)


class InMemoryUsageStore:
    """进程内 ring 档：per-tenant 实例（由 TenantServices 装配），一把锁。"""

    def __init__(self, ring_size: int = USAGE_RING_SIZE) -> None:
        self._lock = threading.Lock()
        self._records: deque[UsageRecord] = deque(maxlen=ring_size)

    def record(self, rec: UsageRecord) -> None:
        with self._lock:
            self._records.append(rec)

    def list_recent(self, limit: int = 50) -> list[dict]:
        bounded = max(1, min(int(limit), 200))
        with self._lock:
            recent = list(self._records)[-bounded:]
        return [rec.public_view() for rec in reversed(recent)]

    def summarize(
        self,
        *,
        since: str | None = None,
        until: str | None = None,
        group_by: str = "caller",
    ) -> list[dict]:
        with self._lock:
            records = list(self._records)
        window = [r for r in records if _in_window(r.ts, since, until)]
        return _aggregate(window, group_by)

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


# ---------------- 归属上下文（AU-D4：contextvar，不改消费方签名） ----------------


@dataclass(frozen=True)
class UsageContext:
    """一次计量归属：身份四元组＋落库 store 引用（无 store ⇒ 上下文存在也不记录）。"""

    tenant_id: str
    run_id: str = ""
    graph_id: str = ""
    node_id: str = ""
    store: Any = None


_usage_context: ContextVar[UsageContext | None] = ContextVar(
    "atlas_llm_usage_context", default=None
)


@contextmanager
def usage_context(
    tenant_id: str,
    *,
    run_id: str = "",
    graph_id: str = "",
    node_id: str = "",
    store: Any = None,
) -> Iterator[UsageContext]:
    """设置归属上下文（API run 入口／nl_generate 端点／反思触发处调用）。

    同线程嵌套安全：内层 reset 只弹内层，外层上下文原样恢复。
    """
    ctx = UsageContext(
        tenant_id=tenant_id,
        run_id=run_id,
        graph_id=graph_id,
        node_id=node_id,
        store=store,
    )
    token = _usage_context.set(ctx)
    try:
        yield ctx
    finally:
        _usage_context.reset(token)


@contextmanager
def usage_node(node_id: str) -> Iterator[None]:
    """节点级 node_id 覆写（loader 每节点执行处调用；保留 tenant/run/graph/store）。

    无外层上下文时是空操作——loader 直跑与单测不计（AU-D4 明写）。
    """
    current = _usage_context.get()
    if current is None:
        yield
        return
    token = _usage_context.set(replace(current, node_id=node_id))
    try:
        yield
    finally:
        _usage_context.reset(token)


def current_context() -> UsageContext | None:
    return _usage_context.get()


# ---------------- 唯一 litellm.completion 封装（AU-D3） ----------------


def _extract_usage(response: Any) -> tuple[int, int, int]:
    """从 ModelResponse 取 (prompt, completion, total) tokens；缺失/坏形状 ⇒ 全 0。

    litellm 的 ModelResponse 属性与下标皆可读；供应商不回 usage 时两者皆空
    （记 0 并 warning，调用方行为不变——U1416）。
    """
    usage = getattr(response, "usage", None)
    if usage is None and isinstance(response, dict):
        usage = response.get("usage")
    if usage is None:
        return 0, 0, 0

    def _token(name: str) -> int:
        value = (
            usage.get(name)
            if isinstance(usage, dict)
            else getattr(usage, name, None)
        )
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            return 0

    prompt = _token("prompt_tokens")
    completion = _token("completion_tokens")
    total = _token("total_tokens") or (prompt + completion)
    return prompt, completion, total


def _safe_record(
    ctx: UsageContext,
    *,
    caller: str,
    model: str,
    status: str,
    latency_ms: float,
    tokens: tuple[int, int, int],
) -> None:
    """旁路落账：store 任何异常只 warning，绝不冒泡给调用方（U1425 反向门）。"""
    try:
        ctx.store.record(
            UsageRecord(
                tenant_id=ctx.tenant_id,
                ts=_now_iso(),
                caller=caller,
                model=model,
                prompt_tokens=tokens[0],
                completion_tokens=tokens[1],
                total_tokens=tokens[2],
                latency_ms=latency_ms,
                status=status,
                node_id=ctx.node_id,
                graph_id=ctx.graph_id,
                run_id=ctx.run_id,
            )
        )
    except Exception:  # noqa: BLE001 - 计量是旁路，失败不许影响运行
        logger.warning(
            "llm usage 记录失败（caller=%s model=%s，不影响运行）", caller, model,
            exc_info=True,
        )


def complete_with_usage(
    kwargs: dict[str, Any], *, caller: str, model: str
) -> tuple[str, dict[str, Any]]:
    """六个调用点的唯一出口：调 litellm、取 content、计量、原样语义返回。

    异常路径：先落 `status="error"` 零 token 条，再**原样抛出**——调用方的
    fail-safe 语义（decision 转人工／condition 走 default／structured 本地兜底）
    一律不吞不改（AU-D5）。
    """
    import litellm

    started = time.monotonic()
    try:
        response = litellm.completion(**kwargs)
    except Exception:
        latency_ms = (time.monotonic() - started) * 1000
        ctx = _usage_context.get()
        if ctx is not None and ctx.store is not None:
            _safe_record(
                ctx,
                caller=caller,
                model=model,
                status="error",
                latency_ms=latency_ms,
                tokens=(0, 0, 0),
            )
        raise
    latency_ms = (time.monotonic() - started) * 1000
    content = response["choices"][0]["message"]["content"]
    prompt, completion, total = _extract_usage(response)
    if total == 0:
        logger.warning(
            "LLM 回包未带 usage（caller=%s model=%s）：token 按 0 记录，调用本身已发生",
            caller,
            model,
        )
    ctx = _usage_context.get()
    if ctx is not None and ctx.store is not None:
        _safe_record(
            ctx,
            caller=caller,
            model=model,
            status="ok",
            latency_ms=latency_ms,
            tokens=(prompt, completion, total),
        )
    return content, {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
        "latency_ms": latency_ms,
    }
