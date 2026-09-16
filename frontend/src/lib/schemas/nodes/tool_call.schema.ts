import type { NodeConfigSchema } from '../metaSchema'

/**
 * tool_call 节点 config schema（04 §4.9；投影自 nodeCatalog.ts 手写规则）。
 * tool 必填非空；params 接受 {{路径}} 模板（x-variable），JSON 形状由 M3 表单化承接。
 * 输出仅 result 根，深层路径以工具 output_schema 为来源（04 §6.5）。
 */
export const toolCallSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    tool: { type: 'string', pattern: '\\S' },
    params: { type: 'string', 'x-variable': true },
  },
  required: ['tool'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      result: {},
    },
  },
}
