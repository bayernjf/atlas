"""操作录制用例：模型与进程内存储（契约 04 §5.11）。

用例在录制时冻结 Graph JSON 快照；RecordingStore 为进程内单例，
重启清空（持久化随 11 S1），/api/demo/reset 不清除（测试资产，同 feedback）。
"""

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class RecordStep(BaseModel):
    node_id: str
    node_type: str
    output: dict[str, Any]


class RecordingCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    graph_id: str
    inputs: dict[str, Any] | None = None
    steps: list[RecordStep] = Field(min_length=1)
    status: str


class RecordingCase(BaseModel):
    id: str
    name: str
    graph: dict[str, Any]
    inputs: dict[str, Any] | None
    steps: list[RecordStep]
    status: str
    created_at: str


class RecordingStore:
    def __init__(self) -> None:
        self._items: list[RecordingCase] = []
        self._counter = 0

    def add(
        self,
        *,
        name: str,
        graph: dict[str, Any],
        inputs: dict[str, Any] | None,
        steps: list[RecordStep],
        status: str,
    ) -> RecordingCase:
        self._counter += 1
        case = RecordingCase(
            id=f"rec-{self._counter}",
            name=name,
            graph=graph,
            inputs=inputs,
            steps=steps,
            status=status,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._items.append(case)
        return case

    def list(self) -> list[RecordingCase]:
        return list(self._items)

    def get(self, case_id: str) -> RecordingCase | None:
        return next((item for item in self._items if item.id == case_id), None)

    def delete(self, case_id: str) -> bool:
        for index, item in enumerate(self._items):
            if item.id == case_id:
                del self._items[index]
                return True
        return False
