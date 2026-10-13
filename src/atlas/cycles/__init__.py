"""自主循环圈层（docs/130 打包 BN＝D69，里程碑 C）。

一圈＝**一次真实 `run_graph`**（不是再套一层 StateGraph），因此每圈都继承既有语义：
审批/事件等待/子图/断点/录制/监控/回放/影子。`engine/` 的 W1 占位骨架一行不改
（docs/88 §3 P-2(a) 冻结继续有效）。

模块分工：

- `state`：圈会话状态、三道预算闸门判定、进程内注册表（纯状态，零 IO）；
- `judge`：终止判定（规则优先，LLM 可选二段）；
- `supervisor`：把两者接到四个运行入口与续跑出口（`loop` 缺省 `None` ⇒ 零行为变化）。

**启用面＝图级 `GraphDSL.loop`**，缺省 `None` ＝ 不启用 ＝ 现状逐字不变。
"""
from .judge import (
    JUDGE_CALLER,
    evaluate_complete_when,
    llm_judge,
    validate_complete_when,
)
from .state import (
    CYCLE_SESSION_RING,
    CycleRegistry,
    CycleRunRef,
    CycleSession,
    budget_exhausted,
    cycle_registry,
)

__all__ = [
    "CYCLE_SESSION_RING",
    "CycleRegistry",
    "CycleRunRef",
    "CycleSession",
    "JUDGE_CALLER",
    "budget_exhausted",
    "cycle_registry",
    "evaluate_complete_when",
    "llm_judge",
    "validate_complete_when",
]
