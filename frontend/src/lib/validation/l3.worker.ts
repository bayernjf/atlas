/**
 * L3 图级校验 Worker 入口（T1 / A-13 / D29）。
 *
 * 只做纯计算：validateL3(nodes, edges) → 诊断数组。不读 DOM、不读编辑器 store。
 * 异常回传 ok:false，由客户端 reject 并触发同步回退。
 */
import { validateL3 } from './l3'
import type { L3WorkerRequest, L3WorkerResponse } from './l3WorkerProtocol'

type WorkerScope = {
  onmessage: ((event: MessageEvent<L3WorkerRequest>) => void) | null
  postMessage: (message: L3WorkerResponse) => void
}

const scope = self as unknown as WorkerScope

scope.onmessage = (event) => {
  const { id, nodes, edges } = event.data
  try {
    scope.postMessage({ id, ok: true, diagnostics: validateL3(nodes, edges) })
  } catch (error) {
    scope.postMessage({
      id,
      ok: false,
      error: error instanceof Error ? error.message : String(error),
    })
  }
}
