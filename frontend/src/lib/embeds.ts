/**
 * 网页嵌入只读面数据层（打包 BQ docs/133 D76）。
 *
 * **写调用面白名单**（docs/94 E-7 / U1124 机检纪律）：本模块消费六只 apiClient 函数
 * （公开只读 `getEmbedReadonlyView`＋administer 写面 `listEmbedConfigs`／`putEmbedConfig`／
 * `createEmbedToken`），`API_WHITELIST` 供测试机检（读本文件源码断言 import 面 ⊆ 白名单）。
 * 页面不直接 import apiClient（U1124 守护不是口头承诺）。
 */
import {
  createEmbedToken,
  getEmbedReadonlyView,
  listEmbedConfigs,
  listGraphs,
  putEmbedConfig,
  type EmbedConfig,
  type EmbedReadonlyView,
  type EmbedTokenResponse,
  type SavedGraphSummary,
} from './apiClient'

export const API_WHITELIST = [
  'getEmbedReadonlyView',
  'listEmbedConfigs',
  'putEmbedConfig',
  'createEmbedToken',
  'listGraphs',
] as const

export {
  createEmbedToken,
  getEmbedReadonlyView,
  listEmbedConfigs,
  listGraphs,
  putEmbedConfig,
}
export type { EmbedConfig, EmbedReadonlyView, EmbedTokenResponse, SavedGraphSummary }

const EMBED_LINK_PREFIX = '/embed/'

/** pathname 以 /embed/ 开头时取末段签名 token；空段/不匹配返回 null（同 approvals 深链纪律）。 */
export function extractEmbedToken(pathname: string): string | null {
  if (!pathname.startsWith(EMBED_LINK_PREFIX)) return null
  const segment = pathname.slice(EMBED_LINK_PREFIX.length).split('/')[0]
  return segment ? decodeURIComponent(segment) : null
}
