/**
 * 试教成图的纯前端逻辑（docs/128，打包 AT）。
 *
 * 存在的理由只有一条：**默认不勾选**。后端 AT-D2 的整条设计就是"机器提议、人确认"，
 * 如果前端把候选预先全勾上再发出去，人就等于没确认过——那是把两阶段降级成一阶段，
 * 而且是最容易在代码评审里看漏的那种降级。
 */

import type { PromotePlan, PromoteSubstitution } from './apiClient'

export type CandidateGroup = {
  nodeId: string
  nodeTitle: string
  items: PromoteSubstitution[]
}

/** 候选按节点分组并保持后端给定的顺序（顺序变了，勾选态就会错位地落到别的行上）。 */
export function groupByNode(
  items: PromoteSubstitution[],
  nodeTitle: (nodeId: string) => string,
): CandidateGroup[] {
  const groups: CandidateGroup[] = []
  const index = new Map<string, CandidateGroup>()
  for (const item of items) {
    const existing = index.get(item.nodeId)
    if (existing) {
      existing.items.push(item)
      continue
    }
    const group: CandidateGroup = { nodeId: item.nodeId, nodeTitle: nodeTitle(item.nodeId), items: [item] }
    index.set(item.nodeId, group)
    groups.push(group)
  }
  return groups
}

export function proposedIds(plan: PromotePlan | null): string[] {
  return (plan?.substitutions ?? []).map((item) => item.id)
}

/**
 * 只有 plan 提议过的 id 才允许发出去（docs/128 §3.2；后端也会 422，
 * 但让按钮先禁用比让用户撞一次错误更诚实）。
 */
export function selectionIsValid(plan: PromotePlan | null, selected: string[]): boolean {
  const known = new Set(proposedIds(plan))
  return selected.every((id) => known.has(id))
}

export function toggleSelection(selected: string[], id: string, on: boolean): string[] {
  if (on) return selected.includes(id) ? selected : [...selected, id]
  return selected.filter((item) => item !== id)
}

export type PlanCounts = {
  proposals: number
  ambiguous: number
  skipped: number
  kept: number
  dropped: number
}

export function countPlan(plan: PromotePlan | null): PlanCounts {
  return {
    proposals: plan?.substitutions.length ?? 0,
    ambiguous: plan?.ambiguous.length ?? 0,
    skipped: plan?.skipped.length ?? 0,
    kept: plan?.prunePreview.keptNodeIds.length ?? 0,
    dropped: plan?.prunePreview.droppedNodeIds.length ?? 0,
  }
}

export function guideFilename(caseId: string, lang: string): string {
  return `atlas-guide-${caseId}-${lang}.md`
}

/**
 * 剪枝提示：只有"确有节点会被剪掉"时开关才可能造成不可编译，但判据不猜——
 * 后端 compile 说了才算（AT-D5）。这里只负责把 dropped 数量讲给人听。
 */
export function pruneWarningCounts(plan: PromotePlan | null): number {
  return plan?.prunePreview.droppedNodeIds.length ?? 0
}
