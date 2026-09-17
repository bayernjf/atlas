/**
 * L3 全图结构预判（M4 批 2 ⑥，08 M4 立项条 / 04 §6.5 末扩展条 / 03 `graph_diagnostics`）。
 *
 * 两条图级规则从后端 `src/atlas/graph/dsl.py` 同构提取（参照 conditions.ts 与后端
 * atlas.graph.conditions 同构的先例），产 layer:'graph' 诊断（M2 仅留类型位、M4 转正）：
 * - GRAPH_ILLEGAL_CYCLE：同构 `_validate_illegal_cycles`——摘除 loop 白名单回边后
 *   DFS 三色检测，只报首个环（遍历序＝节点数组序，邻接序＝边数组序）。
 * - GRAPH_UNREACHABLE：同构 `_validate_reachability`——从 trigger 沿出边 BFS，
 *   不可达节点逐条码；图中无 trigger 时跳过。
 * 聚合顺序同构 validate_graph_report：先环后不可达（Problems 面板再统一经 rank 排序）。
 *
 * 前端 L3 只做实时预判，后端 dsl.py 仍是图级规则唯一权威（不搬后端重跑、M4 不改后端）。
 * 分支完备已由 condition L1（defaultTarget 必填）覆盖、悬空连线由 L2 REF_NODE_NOT_FOUND
 * 覆盖，loop/parallel 等节点的配置与拓扑问题不在本模块两条规则内，不重复产条。
 *
 * 唯一有意偏差：GRAPH_UNREACHABLE 的 loc.nodeId 取不可达节点自身——后端 locations
 * 侧车对图级错误不出条目，前端为 Problems 面板点击定位补上 nodeId（message/规则语义不变）。
 */
import type { ScopeEdgeLike, ScopeNodeLike } from '../scope'
import type { Diagnostic } from './diagnostics'

export const GRAPH_UNREACHABLE_CODE = 'GRAPH_UNREACHABLE'
export const GRAPH_ILLEGAL_CYCLE_CODE = 'GRAPH_ILLEGAL_CYCLE'

const CYCLE_MESSAGE_PREFIX = '检测到非法循环依赖（循环只允许经循环节点的循环体回到自身）：'

type LoopTarget = { valid: false } | { valid: true; body: string; exit: string | null }

/**
 * 归一化 loop 的 body/exit 目标（同构 _validate_loop_config 403-421 的空值/自身判定）：
 * 非空字符串且不等于自身才有效；不存在的幽灵 id 保留（后端另出配置问题，不影响白名单口径）。
 */
function resolveLoopTargets(node: ScopeNodeLike): LoopTarget {
  const isValidTarget = (value: unknown): value is string =>
    typeof value === 'string' && !!value.trim() && value !== node.id
  const rawBody = node.config?.bodyTarget
  const rawExit = node.config?.exitTarget
  if (!isValidTarget(rawBody)) return { valid: false }
  return { valid: true, body: rawBody, exit: isValidTarget(rawExit) ? rawExit : null }
}

/** 沿出边集合 BFS（同构 dsl.py _bfs；stop 节点不入集、不展开）。 */
function bfsOutgoing(
  start: string,
  outgoing: Map<string, Set<string>>,
  stop: Set<string>,
): Set<string> {
  const seen = new Set<string>()
  const queue = [start]
  while (queue.length > 0) {
    const current = queue.pop()!
    if (seen.has(current) || stop.has(current)) continue
    seen.add(current)
    for (const neighbor of outgoing.get(current) ?? []) {
      if (!seen.has(neighbor) && !stop.has(neighbor)) queue.push(neighbor)
    }
  }
  return seen
}

/**
 * loop 白名单回边集合（同构 _validate_loop_config 441-464 产出的 backedges）：
 * 仅当 body 有效且 exit 非空/非自身时，对循环体 BFS（stop＝loop 自身与 exit）中
 * **确有一条边回到 loop 节点**的成员登记 (member, loopId)。
 */
export function loopBackedges(nodes: ScopeNodeLike[], edges: ScopeEdgeLike[]): Set<string> {
  const nodeIds = new Set(nodes.map((node) => node.id))
  const outgoing = new Map<string, Set<string>>()
  for (const edge of edges) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) continue
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, new Set())
    outgoing.get(edge.source)!.add(edge.target)
  }

  const backedges = new Set<string>()
  for (const node of nodes) {
    if (node.kind !== 'loop') continue
    const targets = resolveLoopTargets(node)
    if (!targets.valid) continue
    // 后端 442：exit_target 为 None/自身时整段不产白名单（幽灵 exit 不阻断 BFS）。
    if (targets.exit === null) continue
    const stop = new Set<string>([node.id, targets.exit])
    const body = bfsOutgoing(targets.body, outgoing, stop)
    for (const member of body) {
      if (outgoing.get(member)?.has(node.id)) {
        backedges.add(`${member} ${node.id}`)
      }
    }
  }
  return backedges
}

/**
 * GRAPH_ILLEGAL_CYCLE：摘除 loop 白名单回边后 DFS 三色（同构
 * `_validate_illegal_cycles`），只报首个环。节点遍历序＝nodes 数组序，
 * 邻接序＝edges 数组序；环路径＝栈中回边目标起至栈顶再回到目标。
 */
export function validateIllegalCycles(
  nodes: ScopeNodeLike[],
  edges: ScopeEdgeLike[],
  whitelist: Set<string> = loopBackedges(nodes, edges),
): Diagnostic[] {
  // 同构后端：邻接表不过滤端点（编辑器不会产生悬空边；后端的端点问题由更早的条目承接）。
  const adjacency = new Map<string, string[]>()
  for (const edge of edges) {
    if (whitelist.has(`${edge.source} ${edge.target}`)) continue
    adjacency.set(edge.source, [...(adjacency.get(edge.source) ?? []), edge.target])
  }

  const WHITE = 0
  const GRAY = 1
  const BLACK = 2
  const color = new Map<string, number>(nodes.map((node) => [node.id, WHITE]))
  const found: string[][] = []

  const dfs = (start: string, stack: string[]): void => {
    color.set(start, GRAY)
    stack.push(start)
    for (const neighbor of adjacency.get(start) ?? []) {
      const neighborColor = color.get(neighbor)
      if (neighborColor === BLACK) continue
      if (neighborColor === GRAY) {
        const begin = stack.indexOf(neighbor)
        found.push([...stack.slice(begin), neighbor])
        return
      }
      dfs(neighbor, stack)
      if (found.length > 0) return
    }
    stack.pop()
    color.set(start, BLACK)
  }

  for (const node of nodes) {
    if (color.get(node.id) === WHITE) dfs(node.id, [])
    if (found.length > 0) break
  }

  const cyclePath = found[0]
  if (!cyclePath) return []
  return [
    {
      severity: 'error',
      layer: 'graph',
      code: GRAPH_ILLEGAL_CYCLE_CODE,
      message: `${CYCLE_MESSAGE_PREFIX}${cyclePath.join(' → ')}`,
      loc: {},
    },
  ]
}

/**
 * GRAPH_UNREACHABLE：从 trigger 沿出边 BFS（同构 `_validate_reachability`）。
 * 无 trigger 返回 []（跳过）；不可达节点按节点数组序逐条码。可达集合与遍历序无关。
 */
export function validateReachability(
  nodes: ScopeNodeLike[],
  edges: ScopeEdgeLike[],
): Diagnostic[] {
  const nodeIds = new Set(nodes.map((node) => node.id))
  const roots = nodes.filter((node) => node.kind === 'trigger' && nodeIds.has(node.id)).map((n) => n.id)
  if (roots.length === 0) return []

  const outgoing = new Map<string, Set<string>>()
  for (const edge of edges) {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) continue
    if (!outgoing.has(edge.source)) outgoing.set(edge.source, new Set())
    outgoing.get(edge.source)!.add(edge.target)
  }

  const reachable = new Set<string>()
  const queue = [...roots]
  while (queue.length > 0) {
    const current = queue.pop()!
    if (reachable.has(current)) continue
    reachable.add(current)
    for (const neighbor of outgoing.get(current) ?? []) {
      if (!reachable.has(neighbor)) queue.push(neighbor)
    }
  }

  return nodes
    .filter((node) => !reachable.has(node.id))
    .map((node) => ({
      severity: 'error' as const,
      layer: 'graph' as const,
      code: GRAPH_UNREACHABLE_CODE,
      message: `节点 ${node.id} 不可达（没有任何入边路径能到达它）`,
      // 偏差（见头注）：补 nodeId 供 Problems 点击定位；后端 locations 对图级错误不出条目。
      loc: { nodeId: node.id },
    }))
}

/**
 * L3 全图预判：聚合顺序同构 validate_graph_report——非法环（至多一条）在前，
 * 不可达（节点序）在后。
 */
export function validateL3(nodes: ScopeNodeLike[], edges: ScopeEdgeLike[]): Diagnostic[] {
  const whitelist = loopBackedges(nodes, edges)
  return [...validateIllegalCycles(nodes, edges, whitelist), ...validateReachability(nodes, edges)]
}
