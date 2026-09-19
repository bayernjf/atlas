/**
 * reverseDeps 反向引用索引（M4 批 3 ⑩⑪，08 M4 立项条⑥ / 04 §6.5 末扩展条 / 20 §2.5）。
 *
 * 反向依赖 = 「谁引用了某节点 id」：
 * - target 类：config 中带 `x-ref: {kinds:['*']}` 的目标选择字段（连线目标语义），
 *   路径表与 `frontend/src/lib/schemas/nodes/*.schema.ts` 的 x-ref 标注对拍（见测试）；
 * - template 类：模板字段（scope.ts templateFields）中 `{{节点id.路径}}` 的引用头段。
 *
 * 消费方：
 * 1. 删除节点：deleteSelectedNode 用 referrersOf 定位全部引用方，统一清 target 引用，
 *    替代批 1 之前的按 kind 硬编码清理；dirty 层只重算引用方（L2 精确收窄）。
 * 2. quickFix v1：L2 REF_NODE_NOT_FOUND（悬空引用）挂「删除悬空引用」动作，
 *    removeDanglingRef 产出删除引用后的 config patch（target 清空 / 模板删 token）。
 * 3. quickFix v2 / 改名联动（D30/B3）：renameNodeRefs 在节点 id 重命名时把全部
 *    target 字段与模板 `{{oldId.…}}` 头段原子改写为新 id（数据依赖环检测为另一模块）。
 */
import type { ScopeNodeLike } from '../scope'
import { extractTemplateRefs, templateFields } from '../scope'
import type { DiagnosticToken } from './diagnostics'

/** 引用形式：target＝目标选择字段；template＝模板字段中的 {{路径}}。 */
export type ReverseDep =
  | {
      kind: 'target'
      /** 引用方节点 id。 */
      referrerId: string
      /** RFC 6901 pointer，相对引用方 config 根，如 /branches/0/target。 */
      pointer: string
      /** 被引用的节点 id（target 值）。 */
      refNodeId: string
    }
  | {
      kind: 'template'
      referrerId: string
      pointer: string
      /** 模板引用头段（首个路径段）的节点 id。 */
      refNodeId: string
      /** token 在所属模板字段文本中的区间（删除引用用）。 */
      token: DiagnosticToken
    }

/**
 * target 类引用字段路径表（8 处，与 schema x-ref 标注对拍，见 reverseDeps.test.ts）：
 * condition branches/<i>/target + defaultTarget；loop bodyTarget/exitTarget；
 * parallel branches/<i>/target + joinTarget；human_approval approvedTarget/rejectedTarget。
 * 数组下标用 RFC6901 数值 token（`{i}` 占位）。
 */
export const TARGET_FIELD_PATHS: Record<string, ReadonlyArray<{ pointer: string; indexed: boolean }>> = {
  condition: [
    { pointer: '/branches/{i}/target', indexed: true },
    { pointer: '/defaultTarget', indexed: false },
  ],
  loop: [
    { pointer: '/bodyTarget', indexed: false },
    { pointer: '/exitTarget', indexed: false },
  ],
  parallel: [
    { pointer: '/branches/{i}/target', indexed: true },
    { pointer: '/joinTarget', indexed: false },
  ],
  human_approval: [
    { pointer: '/approvedTarget', indexed: false },
    { pointer: '/rejectedTarget', indexed: false },
  ],
}

/** RFC6901 token 解码：`~0` → `~`，`~1` → `/`（与 diagnostics.escapePointerToken 互逆）。 */
function unescapePointerToken(token: string): string {
  return token.replace(/~1/g, '/').replace(/~0/g, '~')
}

/** pointer → 路径段（RFC6901 解码、去空）。 */
export function pointerSegments(pointer: string): string[] {
  return pointer
    .split('/')
    .filter((segment) => segment.length > 0)
    .map(unescapePointerToken)
}

/** 读取 pointer 处的字符串值（模板字段）；不存在或非字符串返回 undefined。 */
export function readStringAt(config: Record<string, unknown>, segments: string[]): string | undefined {
  let value: unknown = config
  for (let index = 0; index < segments.length; index++) {
    if (value === null || typeof value !== 'object') return undefined
    value = (value as Record<string, unknown>)[segments[index]]
  }
  return typeof value === 'string' ? value : undefined
}

/** 读取 pointer 处的 target 字符串值（indexed 数组分支用下标段）。 */
function readTargetAt(config: Record<string, unknown>, pointer: string): string | null {
  const segments = pointerSegments(pointer)
  if (segments.length < 1) return null
  // 数组分支：…/branches/<i>/target → 取 <i> 下标的 target；普通字段：…/<field>
  if (segments.length >= 3 && /^\d+$/.test(segments[segments.length - 2])) {
    const container = config[segments[segments.length - 3]]
    if (!Array.isArray(container)) return null
    const item = container[Number(segments[segments.length - 2])]
    if (item && typeof item === 'object') {
      const value = (item as Record<string, unknown>)[segments[segments.length - 1]]
      return typeof value === 'string' ? value : null
    }
    return null
  }
  const value = config[segments[segments.length - 1]]
  return typeof value === 'string' ? value : null
}

/**
 * 构建反向引用索引。
 * 全图扫描 O(n)：把每个节点 config 的 target 字段与模板字段引用登记到被引用 id 下。
 * 节点数（几十～几百）下重建成本可忽略；增量维护留待真正过阈值后（BENCHMARK 实证）。
 */
export function buildReverseIndex(nodes: ScopeNodeLike[]): {
  /** 引用给定节点 id 的全部位置（target + 模板），按引用方拓扑数组序稳定。 */
  referrersOf: (nodeId: string) => ReverseDep[]
} {
  const index = new Map<string, ReverseDep[]>()
  const push = (refNodeId: string, dep: ReverseDep) => {
    const list = index.get(refNodeId)
    if (list) list.push(dep)
    else index.set(refNodeId, [dep])
  }

  for (const node of nodes) {
    const config = node.config ?? {}
    // target 类
    for (const field of TARGET_FIELD_PATHS[node.kind] ?? []) {
      if (!field.indexed) {
        const value = readTargetAt(config, field.pointer)
        if (value) push(value, { kind: 'target', referrerId: node.id, pointer: field.pointer, refNodeId: value })
        continue
      }
      // 数组分支：/branches/<i>/target，逐分支登记
      const segments = pointerSegments(field.pointer)
      const arrayKey = segments[segments.length - 3]
      const fieldKey = segments[segments.length - 1]
      const branches = config[arrayKey]
      if (!Array.isArray(branches)) continue
      for (let i = 0; i < branches.length; i++) {
        const item = branches[i]
        if (!item || typeof item !== 'object') continue
        const value = (item as Record<string, unknown>)[fieldKey]
        if (typeof value === 'string' && value) {
          const pointer = `/branches/${i}/target`
          push(value, { kind: 'target', referrerId: node.id, pointer, refNodeId: value })
        }
      }
    }
    // template 类：模板字段中的 {{节点id.…}} 引用（头段为存在的节点 id 才登记）
    for (const field of templateFields(node.kind, config)) {
      for (const ref of extractTemplateRefs(field.text)) {
        const head = ref.path.split('.')[0]?.trim()
        if (!head) continue
        if (head !== 'global') push(head, {
          kind: 'template',
          referrerId: node.id,
          pointer: field.pointer,
          refNodeId: head,
          token: { start: ref.start, end: ref.end, raw: ref.raw },
        })
      }
    }
  }

  return {
    referrersOf: (nodeId) => index.get(nodeId) ?? [],
  }
}

/** 把 segments 末端字段替换为新值，返回根级浅 patch（只动最深容器）。 */
function patchLeaf(
  config: Record<string, unknown>,
  segments: string[],
  newValue: unknown,
): Record<string, unknown> {
  if (segments.length === 1) return { [segments[0]]: newValue }
  // 数组分支：…/branches/<i>/target → 重建 branches 数组
  if (segments.length === 3 && /^\d+$/.test(segments[1])) {
    const container = config[segments[0]]
    if (!Array.isArray(container)) return {}
    const copy = container.map((item, index) =>
      index === Number(segments[1])
        ? { ...(item && typeof item === 'object' ? item : {}), [segments[2]]: newValue }
        : item,
    )
    return { [segments[0]]: copy }
  }
  // 嵌套对象：/inputs/<键> 等
  if (segments.length === 2) {
    const container = config[segments[0]]
    if (!container || typeof container !== 'object') return {}
    return { [segments[0]]: { ...(container as Record<string, unknown>), [segments[1]]: newValue } }
  }
  return {}
}

/**
 * 删除悬空引用的 config patch（quickFix v1 唯一动作，20 §2.5 法定）：
 * - template：把 pointer 字段文本中的 token 区间删除（token.start/end 基于字段原始文本）；
 * - target：把 pointer 处的 target 值清空（''，与删除节点时的自动清理口径一致）。
 * 无法定位（字段缺失/非字符串/token 越界）返回 null，调用方静默跳过。
 */
export function removeDanglingRef(
  kind: string,
  config: Record<string, unknown>,
  pointer: string,
  token?: DiagnosticToken,
): Record<string, unknown> | null {
  if (token) {
    const segments = pointerSegments(pointer)
    const text = readStringAt(config, segments)
    if (text === undefined) return null
    const start = Math.max(0, token.start)
    const end = Math.min(text.length, token.end)
    if (start >= end) return null
    return patchLeaf(config, segments, text.slice(0, start) + text.slice(end))
  }
  // target 类：仅处理路径表中声明过的字段（防误清未声明字段）；数组分支的实际下标与 {i} 占位对齐
  const declared = (TARGET_FIELD_PATHS[kind] ?? []).some((field) => {
    if (!field.indexed) return field.pointer === pointer
    const actual = pointerSegments(pointer)
    const pattern = pointerSegments(field.pointer)
    return (
      actual.length === pattern.length &&
      pattern.every((segment, index) =>
        segment === '{i}' ? /^\d+$/.test(actual[index]) : segment === actual[index],
      )
    )
  })
  if (!declared) return null
  const segments = pointerSegments(pointer)
  return patchLeaf(config, segments, '')
}

/** 转义正则元字符（节点 id 理论上仅字母数字-_，仍保险转义）。 */
function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * 在单个模板字段文本中，把所有头段等于 oldId 的 `{{oldId.…}}` / `{{ oldId }}`
 * 引用头段改写为 newId（保留空白与后续路径）；无匹配返回原文。从后向前 splice 保偏移。
 */
export function rewriteTemplateHeads(text: string, oldId: string, newId: string): string {
  const matched = extractTemplateRefs(text).filter(
    (ref) => (ref.path.split('.')[0] ?? '').trim() === oldId,
  )
  if (matched.length === 0) return text
  const headRegex = new RegExp(
    '^(\\{\\{\\s*)' + escapeRegExp(oldId) + '(\\s*(?:\\.|\\}\\}))',
  )
  let out = text
  for (const ref of [...matched].sort((a, b) => b.start - a.start)) {
    const updatedRaw = ref.raw.replace(headRegex, `$1${newId}$2`)
    out = out.slice(0, ref.start) + updatedRaw + out.slice(ref.end)
  }
  return out
}

/**
 * 节点 id 重命名联动（D30/B3）：扫描全部 target 字段与模板引用，把引用 oldId 的位置
 * 改写为 newId，返回 引用方节点 id -> 合并后的完整 config（调用方浅合并回节点即可）。
 * 同一引用方的多个字段 / 分支 / token 折叠为单个 config；被重命名节点自身不在结果中
 * （其 id 由调用方在节点数组上直接改）。
 */
export function renameNodeRefs(
  nodes: ScopeNodeLike[],
  oldId: string,
  newId: string,
): Map<string, Record<string, unknown>> {
  const edits = new Map<string, Record<string, unknown>>()
  const deps = buildReverseIndex(nodes).referrersOf(oldId)
  for (const dep of deps) {
    const node = nodes.find((candidate) => candidate.id === dep.referrerId)
    if (!node) continue
    const base = edits.get(dep.referrerId) ?? { ...(node.config ?? {}) }
    let patch: Record<string, unknown>
    if (dep.kind === 'target') {
      patch = patchLeaf(base, pointerSegments(dep.pointer), newId)
    } else {
      const segments = pointerSegments(dep.pointer)
      const text = readStringAt(base, segments)
      if (text === undefined) continue
      const rewritten = rewriteTemplateHeads(text, oldId, newId)
      if (rewritten === text) continue
      patch = patchLeaf(base, segments, rewritten)
    }
    if (Object.keys(patch).length > 0) edits.set(dep.referrerId, { ...base, ...patch })
  }
  return edits
}
