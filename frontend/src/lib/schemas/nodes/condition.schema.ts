import type { NodeConfigSchema } from '../metaSchema'

/**
 * condition 节点 config schema（04 §5.2 / §4.9；投影自 nodeCatalog.ts 手写规则）。
 * 两种判定模式（conditionMode）：rule 分支用 expression，llm 分支用 description。
 * 模式相关的必填/互斥、分支名/目标唯一性、defaultTarget 互异、表达式语法均为
 * 白名单无法表达规则，由手写 L1 校验保留。
 */
export const conditionSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    conditionMode: {
      type: 'string',
      enum: ['rule', 'llm'],
      default: 'rule',
      'x-widget': 'radio',
    },
    classifierPrompt: { type: 'string' },
    branches: {
      type: 'array',
      minItems: 1,
      items: {
        type: 'object',
        properties: {
          label: { type: 'string', pattern: '\\S' },
          expression: { type: 'string', pattern: '\\S', 'x-variable': true },
          description: { type: 'string', pattern: '\\S' },
          target: {
            type: 'string',
            pattern: '\\S',
            'x-ref': { kinds: ['*'] },
            'x-widget': 'target-select',
          },
        },
        required: ['label', 'target'],
      },
    },
    defaultTarget: {
      type: 'string',
      pattern: '\\S',
      'x-ref': { kinds: ['*'] },
      'x-widget': 'target-select',
    },
  },
  required: ['branches', 'defaultTarget'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      mode: {},
      branch: {},
      target: {},
    },
  },
}
