"""打包 AC（docs/110）：evaluation run 进程内 store（内存档）。

评估结果属审计价值数据，生产档走 PG（`evaluation/pg_store.py`，迁移 047）；
内存档供非 PG 部署与测试，reset 同清。
"""

from __future__ import annotations

from typing import Any

from .models import EvaluationRun


class EvaluationStore:
    """进程内两档之一：最新 N 条保存在内存，reset 清空。"""

    def __init__(self, capacity: int = 200) -> None:
        self._capacity = capacity
        self._runs: dict[str, EvaluationRun] = {}
        self._order: list[str] = []

    def save(self, run: EvaluationRun) -> None:
        if run.id not in self._runs:
            self._order.append(run.id)
        self._runs[run.id] = run
        while len(self._order) > self._capacity:
            oldest = self._order.pop(0)
            self._runs.pop(oldest, None)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        ids = self._order[-limit:][::-1]
        return [self._runs[i].model_dump(mode="json") for i in ids]

    def clear(self) -> None:
        self._runs.clear()
        self._order.clear()
