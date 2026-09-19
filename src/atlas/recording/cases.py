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
    graph_id: str = ""  # M9 纯超集：所属图 id（发布门禁筛选）；旧用例为空串不入选
    graph: dict[str, Any]
    inputs: dict[str, Any] | None
    steps: list[RecordStep]
    status: str
    created_at: str
    # C（docs/27 §2.4）：录制时钟锚点（ISO UTC）。回放冻结到该时刻供 today()/now() 求值；
    # 新用例入库即生成（与 created_at 同刻），历史用例缺省 None → 回放回退 created_at。
    recorded_at: str | None = None
    # D26 纯超集：录制时递归冻结的 subgraph 引用快照（key＝节点 config.graphId 引用原文，
    # 含 @N 钉版）。单用例冻结回放「内联优先」解析，使引用子图被 reset/删除/改动后
    # 用例仍可回放；旧用例缺省空 dict（回退租户实时 store，保持旧行为）。
    subgraphs: dict[str, dict[str, Any]] = Field(default_factory=dict)


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
        graph_id: str = "",
        subgraphs: dict[str, dict[str, Any]] | None = None,
        recorded_at: str | None = None,
    ) -> RecordingCase:
        self._counter += 1
        stamp = datetime.now(timezone.utc).isoformat()
        case = RecordingCase(
            id=f"rec-{self._counter}",
            name=name,
            graph_id=graph_id,
            graph=graph,
            inputs=inputs,
            steps=steps,
            status=status,
            created_at=stamp,
            recorded_at=recorded_at or stamp,
            subgraphs=subgraphs or {},
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
