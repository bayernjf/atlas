/**
 * L3 Worker 客户端测试（T1 / A-13 / D29）：请求 id 关联、Worker 报错回退、卸载拒绝。
 * 不依赖真实 Worker；用假端口模拟 message/error/terminate。
 */
import { describe, expect, it } from 'vitest'
import type { ScopeEdgeLike, ScopeNodeLike } from '../../scope'
import type { Diagnostic } from '../diagnostics'
import type { L3WorkerRequest, L3WorkerResponse } from '../l3WorkerProtocol'
import { createL3WorkerClient, validateL3WithFallback } from '../l3WorkerClient'

type FakeWorker = Worker & {
  messages: L3WorkerRequest[]
  terminated: boolean
  respond: (response: L3WorkerResponse) => void
  fail: (message?: string) => void
}

function makeFakeWorker(): FakeWorker {
  const listeners = new Map<string, EventListener[]>()
  const messages: L3WorkerRequest[] = []
  let terminated = false

  const worker = {
    messages,
    get terminated() {
      return terminated
    },
    postMessage(message: L3WorkerRequest) {
      messages.push(message)
    },
    addEventListener(type: string, listener: EventListener) {
      const list = listeners.get(type) ?? []
      list.push(listener)
      listeners.set(type, list)
    },
    removeEventListener(type: string, listener: EventListener) {
      listeners.set(
        type,
        (listeners.get(type) ?? []).filter((item) => item !== listener),
      )
    },
    terminate() {
      terminated = true
    },
    respond(response: L3WorkerResponse) {
      for (const listener of listeners.get('message') ?? []) {
        listener(new MessageEvent('message', { data: response }))
      }
    },
    fail(message = 'worker boom') {
      const event = new Event('error')
      Object.defineProperty(event, 'message', { value: message })
      for (const listener of listeners.get('error') ?? []) listener(event)
    },
  }

  return worker as unknown as FakeWorker
}

const nodes: ScopeNodeLike[] = [
  { id: 'trigger-1', kind: 'trigger', config: { triggerType: 'manual' } },
]
const edges: ScopeEdgeLike[] = []
const diagnostic: Diagnostic = {
  severity: 'error',
  layer: 'graph',
  code: 'GRAPH_UNREACHABLE',
  message: '节点 tool-1 不可达（没有任何入边路径能到达它）',
  loc: { nodeId: 'tool-1' },
}

describe('L3 Worker 客户端', () => {
  it('按自增 id 关联并发请求，乱序响应不串台', async () => {
    const worker = makeFakeWorker()
    const client = createL3WorkerClient(() => worker)
    const first = client.validate(nodes, edges)
    const second = client.validate(nodes, edges)

    expect(worker.messages.map((message) => message.id)).toEqual([1, 2])
    worker.respond({ id: 2, ok: true, diagnostics: [] })
    worker.respond({ id: 1, ok: true, diagnostics: [diagnostic] })

    await expect(first).resolves.toEqual([diagnostic])
    await expect(second).resolves.toEqual([])
    client.dispose()
  })

  it('Worker error 拒绝未决请求，validateL3WithFallback 回退同步结果', async () => {
    const worker = makeFakeWorker()
    const client = createL3WorkerClient(() => worker)
    const pending = validateL3WithFallback(client, nodes, edges, () => [diagnostic])

    worker.fail('worker crashed')
    await expect(pending).resolves.toEqual([diagnostic])
    client.dispose()
  })

  it('Worker 不可用时直接走同步 fallback', async () => {
    await expect(validateL3WithFallback(null, nodes, edges, () => [diagnostic])).resolves.toEqual([
      diagnostic,
    ])
  })

  it('dispose 拒绝未决请求并终止 Worker', async () => {
    const worker = makeFakeWorker()
    const client = createL3WorkerClient(() => worker)
    const pending = client.validate(nodes, edges)
    client.dispose()

    await expect(pending).rejects.toThrow('L3 worker disposed')
    expect(worker.terminated).toBe(true)
  })
})
