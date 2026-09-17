/**
 * 前端校验分层性能基准（M4 批 3 ⑫，08 M4 批 3 / BENCHMARK.md）。
 *
 * 运行（零新依赖；常规 `pnpm test` 默认跳过，不进套件计时）：
 *   RUN_BENCH=1 pnpm test bench/validation.perf.test.ts   # macOS/Linux/CI
 *   $env:RUN_BENCH=1; pnpm test bench/validation.perf.test.ts  # Windows PowerShell
 *
 * 负载＝链式图：trigger → tool-1 → … → tool-(N-1)，每个工具节点 params 模板引用
 * 直接上游输出（L2 可见性/路径校验逐节点有活干），N ∈ {200, 500}。
 * 每场景 warmup 10 次 + 采样 50 次，报 p50/p95 单次 ms，结果手抄 BENCHMARK.md 前端校验节。
 * 宽松上限断言只防数量级退化（CI 共享 runner 也稳），性能回归以 BENCHMARK.md 锚点对比为准。
 */
import { describe, test, expect } from 'vitest'
import { validateNodeL1, validateGraph, topologicalOrder } from '../src/lib/validation/validateGraph'
import { validateL3 } from '../src/lib/validation/l3'
import { ValidationEngine } from '../src/lib/validation/engine'
import type { ScopeNodeLike } from '../src/lib/scope'

const enabled = process.env.RUN_BENCH === '1'
const SAMPLES = 50
const WARMUP = 10

type EngineNode = { id: string; data: { kind: string; label: string; config: Record<string, unknown> } }

function makeChain(n: number) {
  const scopeNodes: ScopeNodeLike[] = [
    { id: 'trigger-1', kind: 'trigger', config: { triggerType: 'manual' } },
    ...Array.from({ length: n - 1 }, (_, index) => ({
      id: `tool-${index + 1}`,
      kind: 'tool_call',
      config: {
        tool: 'shop/list_pending_refunds',
        params: index === 0 ? '{}' : `{{tool-${index}.result.order_id}}`,
      },
    })),
  ]
  const edges = Array.from({ length: n - 1 }, (_, index) => ({
    id: `e-${index}`,
    source: index === 0 ? 'trigger-1' : `tool-${index}`,
    target: `tool-${index + 1}`,
  }))
  const engineNodes: EngineNode[] = scopeNodes.map((node) => ({
    id: node.id,
    data: { kind: node.kind, label: node.id, config: node.config ?? {} },
  }))
  return { scopeNodes, engineNodes, edges }
}

function measure(label: string, fn: () => void): { label: string; p50: number; p95: number } {
  for (let i = 0; i < WARMUP; i++) fn()
  const times: number[] = []
  for (let i = 0; i < SAMPLES; i++) {
    const start = performance.now()
    fn()
    times.push(performance.now() - start)
  }
  times.sort((a, b) => a - b)
  const p50 = times[Math.floor(SAMPLES / 2)]
  const p95 = times[Math.floor(SAMPLES * 0.95)]
  // eslint-disable-next-line no-console
  console.log(`[bench] ${label}: p50=${p50.toFixed(3)}ms p95=${p95.toFixed(3)}ms`)
  return { label, p50, p95 }
}

describe.skipIf(!enabled)('editor validation benchmark (RUN_BENCH=1)', () => {
  for (const n of [200, 500]) {
    describe(`${n} nodes`, () => {
      const { scopeNodes, engineNodes, edges } = makeChain(n)
      const allIds = engineNodes.map((node) => node.id)
      const dataById = new Map(engineNodes.map((node) => [node.id, node.data]))
      const middleId = `tool-${Math.floor(n / 2)}`
      topologicalOrder(scopeNodes, edges)

      test('L1/L2/L3 timings', () => {        const results = [
          measure(`N=${n} L1 full`, () => {
            for (const node of engineNodes) validateNodeL1(node.id, node.data)
          }),
          measure(`N=${n} L2 cold (new engine + full run)`, () => {
            const engine = new ValidationEngine()
            engine.runL2(scopeNodes, edges, [], dataById, allIds)
          }),
          (() => {
            const engine = new ValidationEngine()
            engine.runL2(scopeNodes, edges, [], dataById, allIds)
            return measure(`N=${n} L2 warm incremental (1 node due)`, () => {
              engine.runL2(scopeNodes, edges, [], dataById, [middleId])
            })
          })(),
          measure(`N=${n} L3 cold`, () => {
            validateL3(scopeNodes, edges)
          }),
          (() => {
            const engine = new ValidationEngine()
            engine.runGraph(scopeNodes, edges, [])
            return measure(`N=${n} L3 warm (signature hit)`, () => {
              engine.runGraph(scopeNodes, edges, [])
            })
          })(),
          measure(`N=${n} validateGraph full (L1+L2+L3)`, () => {
            validateGraph(
              engineNodes.map((node) => ({ id: node.id, data: node.data })) as never,
              edges,
              [],
            )
          }),
        ]
        // 宽松上限：500 节点全量聚合 < 2s（防数量级退化，非性能 SLO）
        const full = results.find((item) => item.label.includes('validateGraph full'))
        expect(full?.p95).toBeLessThan(2000)
      }, 30000)
    })
  }
})
