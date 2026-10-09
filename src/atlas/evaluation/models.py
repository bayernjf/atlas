"""打包 AC（docs/110）：evaluation_task 落码模型（docs/06 §9.2 形状正式化）。

- 形状权威：docs/110 §2.1；docs/06 §9.2 代码示例。
- 校验：test_cases ≥1 且 ≤200、metrics 仅白名单三值、每个 case 必须声明
  expected.action 或 expected.verify（端点校验失败统一 422 EVALUATION_TASK_INVALID）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

EvaluationMetric = Literal["task_success_rate", "average_steps", "decision_accuracy"]

KNOWN_METRICS: tuple[str, ...] = (
    "task_success_rate",
    "average_steps",
    "decision_accuracy",
)


class ExpectedAction(BaseModel):
    """test_case 预期：action 匹配语义动作；verify 为白名单表达式断言。至少一项。"""

    action: str | None = None
    verify: str | None = None


class TestCase(BaseModel):
    """评估用例（docs/06 §9.2）。__test__ = False 防止 pytest 误收集。"""

    __test__ = False
    name: str | None = None
    inputs: dict[str, Any] = Field(default_factory=dict)
    expected: ExpectedAction | None = None

    @model_validator(mode="after")
    def _expected_required(self) -> "TestCase":
        if self.expected is None or (
            self.expected.action is None and self.expected.verify is None
        ):
            raise ValueError("每个测试用例必须声明 expected.action 或 expected.verify")
        return self


class EvaluationTask(BaseModel):
    task_id: str = Field(min_length=1, max_length=64)
    description: str | None = None
    test_cases: list[TestCase] = Field(min_length=1, max_length=200)
    metrics: list[EvaluationMetric] = Field(
        default_factory=lambda: list(KNOWN_METRICS)
    )

    @model_validator(mode="after")
    def _metrics_unique(self) -> "EvaluationTask":
        if len(set(self.metrics)) != len(self.metrics):
            raise ValueError("metrics 不能重复")
        return self


class EvaluationCaseResult(BaseModel):
    """case 级结果：passed＝verify 通过（未声明 verify 恒 true）；decision_matched
    仅声明 action 时非 None；error 记运行期异常 / verify 非法。"""

    name: str
    passed: bool
    decision_matched: bool | None = None
    steps: int = 0
    error: str | None = None


class EvaluationSummary(BaseModel):
    """metrics 三元组（docs/06 §9.2）：decision_accuracy 分母为 0 时 null。"""

    task_success_rate: float
    average_steps: float
    decision_accuracy: float | None = None


class EvaluationRun(BaseModel):
    """一次评估批的结果快照（持久化形状，docs/110 §2.1）。"""

    id: str
    task_id: str
    graph_id: str
    summary: EvaluationSummary
    cases: list[EvaluationCaseResult]
    created_at: str
