/**
 * 工具 schema 来源（M3 第二来源，08 M3 立项条② / 03 `form_renderer` / 04 §4.10）。
 *
 * 工具 input_schema 取自 `GET /api/adapters` 的发现快照，键 `<adapter>/<tool>`；
 * 表里存的是发现结果里的同一个对象引用，不复制、不改写（后端构造 Capability 时
 * 已按 04 §4.9 的 20 keyword 白名单校验过形状，故可直接当 MetaSchema 用）。
 * `lib/schemas` 的节点注册表维持 M1 的单一只读来源，本模块只做第二个来源的视图。
 *
 * 未注册工具、发现未就绪/失败、空 schema（如 shop/list_pending_refunds 的 `{}`）
 * 一律判定为不可表单化，由调用方降级旧 JSON 文本框。
 */
import type { AdapterInfo } from '../apiClient'
import type { MetaSchema } from '../schemas/metaSchema'
import { resolveWidget } from './resolveWidget'

/** 发现快照 → `<adapter>/<tool>` 的 input_schema 表（持引用，不复制）。 */
export function buildToolSchemaTable(
  adapters: AdapterInfo[] | null | undefined,
): Record<string, MetaSchema> {
  const table: Record<string, MetaSchema> = {}
  for (const adapter of adapters ?? []) {
    for (const tool of adapter.tools) {
      table[`${adapter.id}/${tool.name}`] = tool.input_schema as MetaSchema
    }
  }
  return table
}

/**
 * 该 schema 能否生成表单：空 schema（`{}`）与根不是可分组对象（无 type 无
 * properties、oneOf 等白名单外结构）都不可表单化，走旧 JSON 文本框。
 */
export function isFormRenderable(schema: MetaSchema | null | undefined): schema is MetaSchema {
  if (!schema) return false
  if (Object.keys(schema).length === 0) return false
  const resolution = resolveWidget(schema, 'tool')
  return resolution.kind === 'group' || resolution.kind === 'keyvalue'
}
