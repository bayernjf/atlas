/**
 * 结构化诊断模型（M2，08 M2 立项条 / 04 §6.5 / 19 §1.5.1）。
 *
 * L1 字段诊断、L2 模板引用诊断共用同一形状；graph 层 M2 仅留类型位，
 * 前端不预判 L3（唯一权威仍在后端 dsl.py）。quickFix M2 仅留类型位，
 * 不产任何动作（首个动作随 M4）。
 */

export type DiagnosticSeverity = 'error' | 'warning'

export type DiagnosticLayer = 'field' | 'template' | 'graph'

export type DiagnosticToken = {
  /** token 在所属模板字段文本中的起始下标（含 {{）。 */
  start: number
  /** 结束下标（不含，含 }}）。 */
  end: number
  /** 源串切片（含 {{}}）。 */
  raw: string
}

export type DiagnosticLoc = {
  /** 所属图节点 id；图级诊断可缺省。 */
  nodeId?: string
  /** RFC 6901 JSON Pointer，相对节点 config 对象根，如 /branches/0/expression。 */
  pointer?: string
  /** L2 模板引用的 token 区间。 */
  token?: DiagnosticToken
}

/** M2 仅留类型位：M2 不产 quickFix 动作，首个动作随 M4。 */
export type QuickFix = {
  id: string
  title: string
}

export type Diagnostic = {
  severity: DiagnosticSeverity
  layer: DiagnosticLayer
  code: string
  /** 中文文案，与 M1/M0 现文案逐条一致；未来 i18n key 同源。 */
  message: string
  loc: DiagnosticLoc
  quickFix?: QuickFix[]
}

/** RFC 6901 token 转义：`~` → `~0`，`/` → `~1`。 */
export function escapePointerToken(token: string): string {
  return token.replace(/~/g, '~0').replace(/\//g, '~1')
}

/**
 * 诊断排序（M2 rank）：error 优先；同严重度按节点拓扑序（nodeOrder 中上游在前，
 * 未知/缺 nodeId 排后并按 id 字典序）；再按 pointer、token.start 稳定排序。
 * 不修改入参，返回新数组。
 */
export function rank(diagnostics: Diagnostic[], nodeOrder: string[] = []): Diagnostic[] {
  const orderIndex = new Map(nodeOrder.map((id, index) => [id, index]))
  const tail = nodeOrder.length
  return diagnostics
    .map((diagnostic, index) => ({ diagnostic, index }))
    .sort((a, b) => {
      const cmp = compare(a.diagnostic, b.diagnostic)
      return cmp !== 0 ? cmp : a.index - b.index
    })
    .map(({ diagnostic }) => diagnostic)

  function compare(x: Diagnostic, y: Diagnostic): number {
    const severity = severityWeight(x) - severityWeight(y)
    if (severity !== 0) return severity
    const node = nodeKey(x) - nodeKey(y)
    if (node !== 0) return node
    const nodeId = (x.loc.nodeId ?? '').localeCompare(y.loc.nodeId ?? '')
    if (nodeId !== 0) return nodeId
    const pointer = (x.loc.pointer ?? '').localeCompare(y.loc.pointer ?? '')
    if (pointer !== 0) return pointer
    return (x.loc.token?.start ?? Number.MAX_SAFE_INTEGER) - (y.loc.token?.start ?? Number.MAX_SAFE_INTEGER)
  }

  function severityWeight(diagnostic: Diagnostic): number {
    return diagnostic.severity === 'error' ? 0 : 1
  }

  function nodeKey(diagnostic: Diagnostic): number {
    const id = diagnostic.loc.nodeId
    if (id === undefined) return tail + 1
    const index = orderIndex.get(id)
    return index === undefined ? tail : index
  }
}
