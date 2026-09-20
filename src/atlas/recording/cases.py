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


class RecordingUpdateRequest(BaseModel):
    """用例元信息编辑（docs/28 §2.3）：仅 name/inputs 可改。

    steps/graph/subgraphs/graph_id/时间戳是录制事实与冻结快照，v1 不可改（请重新录制）。
    字段缺省（None）表示不改；name 显式空串/超长由 pydantic 校验 422。
    """

    name: str | None = Field(default=None, min_length=1, max_length=100)
    inputs: dict[str, Any] | None = None


class ReplayRequest(BaseModel):
    """单用例回放可选请求体（docs/28 §2.2/§2.3）。

    mock_tools=true 时以录制桩 output 替代真实适配器调用（隔离外部系统，发布门禁不接）；
    inputs_override 顶层键浅合并进 case.inputs（一次性入参参数化，不落库）。
    """

    mock_tools: bool = False
    inputs_override: dict[str, Any] | None = None


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

    def update_meta(
        self,
        case_id: str,
        *,
        name: str | None = None,
        inputs: dict[str, Any] | None = None,
    ) -> RecordingCase | None:
        """仅改 name/inputs（录制事实不可改）；不存在返 None。无变更字段时原样返回。"""
        for index, item in enumerate(self._items):
            if item.id == case_id:
                changes: dict[str, Any] = {}
                if name is not None:
                    changes["name"] = name
                if inputs is not None:
                    changes["inputs"] = inputs
                if changes:
                    updated = item.model_copy(update=changes)
                    self._items[index] = updated
                    return updated
                return item
        return None

    def delete(self, case_id: str) -> bool:
        for index, item in enumerate(self._items):
            if item.id == case_id:
                del self._items[index]
                return True
        return False
