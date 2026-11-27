/**
 * L3 图级校验的 Worker 协议（T1 / A-13 / D29）。
 *
 * 只传结构化可克隆的节点/边最小投影；诊断数组原样回传。
 * 协议与 Worker 实现分离，便于 Worker 客户端在 Node/vitest 下用假端口测试。
 */
import type { ScopeEdgeLike, ScopeNodeLike } from '../scope'
import type { Diagnostic } from './diagnostics'

export type L3WorkerRequest = {
  id: number
  nodes: ScopeNodeLike[]
  edges: ScopeEdgeLike[]
}

export type L3WorkerResponse =
  | { id: number; ok: true; diagnostics: Diagnostic[] }
  | { id: number; ok: false; error: string }
