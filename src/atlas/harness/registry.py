"""适配器注册发现（04 §4.4）：注册、动态发现、心跳健康。

Demo 阶段为进程内注册表（与 T4 进程内事件总线一致）；Phase 2 Go 网关化时
替换为网关侧注册中心，接口保持不变。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from .base import Capability, HarnessAdapter

# 超过该秒数无心跳即视为不可用（编辑后台置灰，04 §4.4）
HEARTBEAT_TTL_SECONDS = 30.0


@dataclass
class Registration:
    adapter: HarnessAdapter
    capabilities: list[Capability]
    last_heartbeat: float


class AdapterRegistry:
    def __init__(
        self,
        *,
        ttl_seconds: float = HEARTBEAT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._clock = clock
        self._adapters: dict[str, Registration] = {}

    def register(self, adapter: HarnessAdapter) -> None:
        if adapter.adapter_id in self._adapters:
            raise ValueError(f"adapter {adapter.adapter_id!r} already registered")
        self._adapters[adapter.adapter_id] = Registration(
            adapter=adapter,
            capabilities=list(adapter.list_capabilities()),
            last_heartbeat=self._clock(),
        )

    def unregister(self, adapter_id: str) -> None:
        self._adapters.pop(adapter_id, None)

    def get(self, adapter_id: str) -> HarnessAdapter:
        return self._adapters[adapter_id].adapter

    def heartbeat(self, adapter_id: str) -> None:
        self._adapters[adapter_id].last_heartbeat = self._clock()

    def is_healthy(self, adapter_id: str) -> bool:
        record = self._adapters.get(adapter_id)
        if record is None:
            return False
        return self._clock() - record.last_heartbeat <= self._ttl

    def list_adapters(self) -> list[dict]:
        """发现接口：供编辑后台查询可用适配器与工具（04 §4.4）。

        注册表内为进程内适配器，被发现即可达：列举前统一续心跳，
        避免 30 秒 TTL 把常驻单例误判不可用；TTL 置灰留给远程注册（D6）。
        """
        now = self._clock()
        for record in self._adapters.values():
            record.last_heartbeat = now
        return [
            {
                "id": record.adapter.adapter_id,
                "type": record.adapter.adapter_type,
                "healthy": self.is_healthy(record.adapter.adapter_id),
                "tools": [
                    {
                        "name": capability.name,
                        "description": capability.description,
                        "permission": capability.permission.value,
                        "idempotent": capability.is_idempotent,
                        "input_schema": capability.input_schema,
                        "output_schema": capability.output_schema,
                    }
                    for capability in record.capabilities
                ],
            }
            for record in self._adapters.values()
        ]
