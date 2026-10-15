/**
 * NL 造工具数据层（打包 BQ docs/133 D71）。
 *
 * **写调用面白名单**（docs/94 E-7 / U1124 机检纪律）：本模块消费四只 apiClient 函数
 * （`listAdapterDrafts`／`createAdapterDraft`／`dryRunAdapterDraft`／`putAdapterDraftDecision`），
 * `API_WHITELIST` 供测试机检。页面不直接 import apiClient。
 * 决策写面仅 admin 可调（docs/133 F-P2：auth_type 非 none 须 authConfirmed=true）。
 */
import {
  createAdapterDraft,
  dryRunAdapterDraft,
  listAdapterDrafts,
  putAdapterDraftDecision,
  type AdapterDraft,
  type AdapterDefinition,
} from './apiClient'

export const API_WHITELIST = [
  'listAdapterDrafts',
  'createAdapterDraft',
  'dryRunAdapterDraft',
  'putAdapterDraftDecision',
] as const

export {
  createAdapterDraft,
  dryRunAdapterDraft,
  listAdapterDrafts,
  putAdapterDraftDecision,
}
export type { AdapterDraft, AdapterDefinition }
