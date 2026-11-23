/**
 * L3 Worker 客户端（T1 / A-13 / D29）。
 *
 * 单 Worker 复用，按自增 id 关联请求/响应；组件卸载或 Worker 报错时拒绝未决请求。
 * 默认工厂用 Vite 原生 Worker 语法；测试可注入假 Worker，避免依赖浏览器 Worker。
 */
import type { ScopeEdgeLike, ScopeNodeLike } from '../scope'
import type { Diagnostic } from './diagnostics'
import type { L3WorkerRequest, L3WorkerResponse } from './l3WorkerProtocol'

export type L3WorkerClient = {
  validate: (nodes: ScopeNodeLike[], edges: ScopeEdgeLike[]) => Promise<Diagnostic[]>
  dispose: () => void
}

type Pending = {
  resolve: (diagnostics: Diagnostic[]) => void
  reject: (error: Error) => void
}

function workerErrorMessage(event: Event): string {
  const maybeError = event as Event & { error?: Error; message?: string }
  return maybeError.error?.message ?? maybeError.message ?? 'L3 worker error'
}

export function createL3WorkerClient(factory: () => Worker = defaultL3WorkerFactory): L3WorkerClient {
  const worker = factory()
  const pending = new Map<number, Pending>()
  let nextId = 0
  let disposed = false
  let failed = false

  const rejectAll = (error: Error) => {
    for (const entry of pending.values()) entry.reject(error)
    pending.clear()
  }

  const onMessage = (event: MessageEvent) => {
    const data = event.data as L3WorkerResponse | undefined
    if (!data || typeof data.id !== 'number') return
    const entry = pending.get(data.id)
    if (!entry) return
    pending.delete(data.id)
    if (data.ok) entry.resolve(data.diagnostics)
    else entry.reject(new Error(data.error || 'L3 worker failed'))
  }

  const onError = (event: Event) => {
    failed = true
    rejectAll(new Error(workerErrorMessage(event)))
  }

  worker.addEventListener('message', onMessage)
  worker.addEventListener('error', onError)
  worker.addEventListener('messageerror', onError)

  return {
    validate(nodes, edges) {
      if (disposed) return Promise.reject(new Error('L3 worker disposed'))
      if (failed) return Promise.reject(new Error('L3 worker failed'))
      const id = ++nextId
      return new Promise<Diagnostic[]>((resolve, reject) => {
        pending.set(id, { resolve, reject })
        try {
          worker.postMessage({ id, nodes, edges } satisfies L3WorkerRequest)
        } catch (error) {
          pending.delete(id)
          reject(error instanceof Error ? error : new Error(String(error)))
        }
      })
    },
    dispose() {
      if (disposed) return
      disposed = true
      rejectAll(new Error('L3 worker disposed'))
      worker.removeEventListener('message', onMessage)
      worker.removeEventListener('error', onError)
      worker.removeEventListener('messageerror', onError)
      worker.terminate()
    },
  }
}

function defaultL3WorkerFactory(): Worker {
  return new Worker(new URL('./l3.worker.ts', import.meta.url), { type: 'module' })
}

/**
 * Worker 优先、同步回退。Worker 不可用/报错/结构化克隆失败时，调用方仍拿到诊断，
 * 不因性能优化把可用性打掉。
 */
export async function validateL3WithFallback(
  client: L3WorkerClient | null,
  nodes: ScopeNodeLike[],
  edges: ScopeEdgeLike[],
  fallback: () => Diagnostic[],
): Promise<Diagnostic[]> {
  if (!client) return fallback()
  try {
    return await client.validate(nodes, edges)
  } catch {
    return fallback()
  }
}
