/**
 * 校验增量调度的脏标记（M4 批 1 ⑤，08 M4 立项条③ / 04 §4.10）。
 *
 * 三层校验（与 l1/l2/l3 对应）：
 * - L1 节点字段层：只依赖单个节点 config，改某节点 config 仅脏该节点 L1；
 * - L2 跨节点引用/模板层：依赖节点间 target 与 `{{路径}}` 引用、全局变量；
 * - L3 全图结构层：不可达/环，依赖节点与边的拓扑，任何结构变更都需重算
 *   （批 2 用结构 hash 记忆化决定是否真正重算）。
 *
 * 本模块批 1 只产「失效范围」的纯状态与单测，保守正确（宁可多重算不可漏）：
 * 尚无 reverseDeps（批 3）/ScopeIndex 记忆化（批 2）时，结构与变量变更按全量 L2 标记，
 * config 编辑按节点收窄 L1、按字段是否承载引用决定是否脏该节点 L2。
 * 分层调度（L1 同步 / L2 防抖 / L3 requestIdleCallback）在批 2 落地并消费本状态。
 */

export type ValidationDirty = {
  /** L1 字段层待重算的节点 id（去重）。 */
  l1NodeIds: string[]
  /** L2 引用层待重算的节点 id（去重）。 */
  l2NodeIds: string[]
  /** L3 全图结构层是否待重算。 */
  l3: boolean
  /** 单调版本号：任何标记失效的变更 +1，供调度器记忆化比对。 */
  revision: number
}

/** 初始：尚未做过全图结构校验（l3 待算）；节点级在首次挂载/换图时补齐。 */
export const INITIAL_DIRTY: ValidationDirty = {
  l1NodeIds: [],
  l2NodeIds: [],
  l3: true,
  revision: 0,
}

/**
 * 判定一次 config patch 是否触及跨节点引用（target 选择或 `{{}}` 模板字段）。
 * 命中则该节点 L2 失效；否则只脏 L1（纯数值/文案字段不影响引用关系）。
 * 键名命中常见引用承载字段，或任一字符串值里出现 `{{`（兜底未列举字段）。
 */
const REFERENCE_BEARING_KEY =
  /(target|branches|expression|template|prompt|summary|params|inputs|subject|body|channel|to)$/i

export function patchTouchesReferences(patch: Record<string, unknown>): boolean {
  for (const [key, value] of Object.entries(patch)) {
    if (REFERENCE_BEARING_KEY.test(key)) return true
    if (typeof value === 'string' && value.includes('{{')) return true
  }
  return false
}

/** 合并节点 id：去重并保持既有顺序，新 id 追加在后。 */
function unionIds(existing: string[], added: string[]): string[] {
  const seen = new Set(existing)
  const merged = [...existing]
  for (const id of added) {
    if (!seen.has(id)) {
      seen.add(id)
      merged.push(id)
    }
  }
  return merged
}

function bump(dirty: ValidationDirty, patch: Partial<ValidationDirty>): ValidationDirty {
  return {
    l1NodeIds: patch.l1NodeIds ?? dirty.l1NodeIds,
    l2NodeIds: patch.l2NodeIds ?? dirty.l2NodeIds,
    l3: patch.l3 ?? dirty.l3,
    revision: dirty.revision + 1,
  }
}

/** loop 节点 bodyTarget/exitTarget 编辑会改变白名单与作用域区域，属结构变更（L3 + 全量 L2）。 */
const LOOP_STRUCTURE_KEYS = new Set(['bodyTarget', 'exitTarget'])

/** 改某节点 config：L1 必脏该节点；patch 承载引用时 L2 也脏该节点。 */
export function markConfigEdit(
  dirty: ValidationDirty,
  nodeId: string,
  patch: Record<string, unknown>,
  context?: { kind?: string; allNodeIds?: string[] },
): ValidationDirty {
  const loopStructureChanged =
    context?.kind === 'loop' &&
    Object.keys(patch).some((key) => LOOP_STRUCTURE_KEYS.has(key))
  return bump(dirty, {
    l1NodeIds: unionIds(dirty.l1NodeIds, [nodeId]),
    l2NodeIds: loopStructureChanged
      ? unionIds(dirty.l2NodeIds, context?.allNodeIds ?? [nodeId])
      : patchTouchesReferences(patch)
        ? unionIds(dirty.l2NodeIds, [nodeId])
        : dirty.l2NodeIds,
    l3: loopStructureChanged ? true : undefined,
  })
}

/** 改节点元信息（如显示名 label）：只脏该节点 L1。 */
export function markNodeMetaEdit(dirty: ValidationDirty, nodeId: string): ValidationDirty {
  return bump(dirty, { l1NodeIds: unionIds(dirty.l1NodeIds, [nodeId]) })
}

/** 新增节点：结构变更（L3）+ 新节点 L1/L2 待算。 */
export function markNodeAdded(dirty: ValidationDirty, nodeId: string): ValidationDirty {
  return bump(dirty, {
    l1NodeIds: unionIds(dirty.l1NodeIds, [nodeId]),
    l2NodeIds: unionIds(dirty.l2NodeIds, [nodeId]),
    l3: true,
  })
}

/** 删除节点：结构变更（L3）；引用被清理、拓扑改变，剩余节点 L2 保守全量重算。 */
export function markNodeDeleted(dirty: ValidationDirty, remainingNodeIds: string[]): ValidationDirty {
  return bump(dirty, {
    l1NodeIds: dirty.l1NodeIds.filter((id) => remainingNodeIds.includes(id)),
    l2NodeIds: [...remainingNodeIds],
    l3: true,
  })
}

/**
 * 边的增删：结构变更（L3）。手动连线/删边可能改变变量可达作用域，
 * L2 至少重算边两端节点；调用方掌握全量节点时也可直接传全量（保守）。
 */
export function markEdgeChanged(dirty: ValidationDirty, endpointNodeIds: string[]): ValidationDirty {
  return bump(dirty, {
    l2NodeIds: unionIds(dirty.l2NodeIds, endpointNodeIds),
    l3: true,
  })
}

/** 换图（载入草稿/模板）：全新拓扑，L3 + 全部节点 L1/L2 待算。 */
export function markGraphLoaded(dirty: ValidationDirty, allNodeIds: string[]): ValidationDirty {
  return bump({ ...INITIAL_DIRTY, revision: dirty.revision }, {
    l1NodeIds: [...allNodeIds],
    l2NodeIds: [...allNodeIds],
    l3: true,
  })
}

/** 全局变量增删：`{{global.x}}` 引用面广且批 1 无反向索引，L2 全量保守重算。 */
export function markVariablesChanged(dirty: ValidationDirty, allNodeIds: string[]): ValidationDirty {
  return bump(dirty, { l2NodeIds: [...allNodeIds] })
}

/** 调度器消费完失效范围后清标记（revision 不动，它只随变更递增）。 */
export function markClean(dirty: ValidationDirty): ValidationDirty {
  return { l1NodeIds: [], l2NodeIds: [], l3: false, revision: dirty.revision }
}

/** 分层调度按批消费的范围（revision 用于防漏：消费期间有新变更则整批不清，下轮重算）。 */
export type ConsumedRanges = {
  revision: number
  l1NodeIds?: string[]
  l2NodeIds?: string[]
  l3?: boolean
}

/**
 * 只清除本次实际消费的范围，且仅当 dirty.revision 与快照一致；
 * 消费期间又发生变更（revision 已增）则原样返回，由调度器下一轮合并重算，保证不漏。
 */
export function markConsumed(dirty: ValidationDirty, ranges: ConsumedRanges): ValidationDirty {
  if (dirty.revision !== ranges.revision) return dirty
  const consumedL1 = new Set(ranges.l1NodeIds ?? [])
  const consumedL2 = new Set(ranges.l2NodeIds ?? [])
  return {
    l1NodeIds: ranges.l1NodeIds ? dirty.l1NodeIds.filter((id) => !consumedL1.has(id)) : dirty.l1NodeIds,
    l2NodeIds: ranges.l2NodeIds ? dirty.l2NodeIds.filter((id) => !consumedL2.has(id)) : dirty.l2NodeIds,
    l3: ranges.l3 ? false : dirty.l3,
    revision: dirty.revision,
  }
}
