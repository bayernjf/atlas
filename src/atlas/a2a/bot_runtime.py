"""A2A Bot 运行时（T3）：每个可管理 Agent 一个独立运行时对象。

边界说明：这是**进程内逻辑运行时**，不是独立进程/容器。每个运行时拥有：

- 独立 `runtime_id`（同一进程内稳定、可审计）；
- 独立 `agent_id`（与 `/api/agents` 的 Agent 配置一一对应）；
- `adapter_ids` 白名单（运行时只装配该 Bot 声明的适配器，未声明的不进注册表）；
- 独立 run 记录：`run_store` 以 `agentId` 归属，`/api/agents/{id}/runs` 可直接过滤。

它解决的是 A-02 遗留的“多个 A2A 调用方共用一个无归属 runtime”：现在每次 run 都能从
task→agentId→runId→run_store 串起来，且不同 Bot 的适配器面不同。
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from atlas.iam.agents_store import Agent


@dataclass(frozen=True)
class BotRuntime:
    bot_id: str
    agent_id: str
    tenant_id: str
    runtime_id: str
    adapter_ids: frozenset[str] = field(default_factory=frozenset)

    def to_record(self) -> dict[str, Any]:
        return {
            "botId": self.bot_id,
            "agentId": self.agent_id,
            "tenantId": self.tenant_id,
            "runtimeId": self.runtime_id,
            "adapterIds": sorted(self.adapter_ids),
        }


class BotRuntimeRegistry:
    """按 `(tenant_id, agent_id)` 缓存 BotRuntime；不缓存 Agent 配置副本。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runtimes: dict[tuple[str, str], BotRuntime] = {}

    def get(self, services: Any, agent_id: str) -> BotRuntime | None:
        agent: Agent | None = services.agents_store.get(services.tenant_id, agent_id)
        if agent is None or not agent.enabled:
            return None
        key = (services.tenant_id, agent.id)
        with self._lock:
            runtime = self._runtimes.get(key)
            if runtime is None:
                runtime = BotRuntime(
                    bot_id=agent.id,
                    agent_id=agent.id,
                    tenant_id=services.tenant_id,
                    runtime_id=f"bot-runtime:{services.tenant_id}:{agent.id}",
                    adapter_ids=frozenset(agent.adapter_ids),
                )
                self._runtimes[key] = runtime
            return runtime

    def reset_for_tests(self) -> None:
        with self._lock:
            self._runtimes.clear()


_bot_runtimes = BotRuntimeRegistry()


def get_bot_runtime(services: Any, agent_id: str) -> BotRuntime | None:
    return _bot_runtimes.get(services, agent_id)


def reset_for_tests() -> None:
    _bot_runtimes.reset_for_tests()
