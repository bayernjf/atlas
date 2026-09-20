/**
 * A 包（docs/27 §3）：子图事件命名空间展示纯逻辑。
 *
 * 子图内部节点的 node_start/node_end 携带 `subgraphPath`（每层父图 subgraph 节点 id），
 * 顶层节点缺省。这里只做可单测的归类与日志前缀格式化；画布状态/录制口径在各自调用点处理。
 */

/** 是否为子图内部节点事件（顶层节点返回 false）。 */
export function isSubgraphInternal(subgraphPath?: string[]): boolean {
  return Array.isArray(subgraphPath) && subgraphPath.length > 0
}

/** 日志前缀：顶层为 ''，单层 `[sg-a] `，嵌套 `[sg-a › sg-b] `。 */
export function subgraphPathPrefix(subgraphPath?: string[]): string {
  if (!isSubgraphInternal(subgraphPath)) return ''
  return `[${(subgraphPath as string[]).join(' › ')}] `
}

/** 审批归属的可读标签（Modal 用）：单层 `sg-a`，嵌套 `sg-a › sg-b`；顶层返回 ''。 */
export function subgraphPathLabel(subgraphPath?: string[]): string {
  if (!isSubgraphInternal(subgraphPath)) return ''
  return (subgraphPath as string[]).join(' › ')
}
