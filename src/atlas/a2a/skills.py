"""Plan 模式 skill 执行器（docs/90）：纯函数，无 DB、无 LLM、无运行副作用。"""

from __future__ import annotations

_SKILL_PLANS: dict[str, dict] = {
    "plan-approval-flow": {
        "required": ["tenant_id", "flow_name"],
        "steps": [
            "确定租户与固定审批 Graph 的触发节点与数据 Schema",
            "规划节点与连线：触发 → 条件分支 → 人工等待(approval) → 通过/拒绝下游",
            "列出挂起审批帧的渠道（web/im/email）与一次性认领约束（docs/62，单副本）",
            "给出发布钉版前的校验与回归清单（release gate + 用例集）",
        ],
    },
    "diagnose-run": {
        "required": ["tenant_id", "run_id"],
        "steps": [
            "读取 run 状态与 span 级追踪，定位最后完成节点",
            "若为 suspended，投影挂起帧（claimed_suspended 标注认领者与时长）",
            "判断等待形态（动态时长/到点/事件）与审批是否已决",
            "给出只读结论与建议；不重放、不续跑、不改状态（重放属未决事项 D36）",
        ],
    },
}


def list_skill_ids() -> list[str]:
    return list(_SKILL_PLANS)


def run_plan_skill(skill_id: str, params: dict) -> dict:
    """返回 plan 结果 dict：state ∈ completed | input-required | failed。"""
    plan = _SKILL_PLANS.get(skill_id)
    if plan is None:
        return {
            "state": "failed",
            "message": f"unknown skill: {skill_id}; available: {', '.join(_SKILL_PLANS)}",
        }
    missing = [key for key in plan["required"] if not params.get(key)]
    if missing:
        return {
            "state": "input-required",
            "message": f"missing required parameters: {', '.join(missing)}",
        }
    if skill_id == "plan-approval-flow":
        subject = f"flow={params['flow_name']}"
    else:
        subject = f"run={params['run_id']}"
    return {
        "state": "completed",
        "name": f"{skill_id}-plan",
        "params": params,
        "steps": plan["steps"],
        "summary": f"{skill_id}: plan generated for tenant={params['tenant_id']} ({subject}).",
    }
