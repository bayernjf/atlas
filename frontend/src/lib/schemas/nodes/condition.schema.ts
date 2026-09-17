import type { NodeConfigSchema } from '../metaSchema'

/**
 * condition 节点 config schema（04 §5.2 / §4.9；投影自 nodeCatalog.ts 手写规则）。
 * 至少一个分支，每分支 label/expression/target 非空；defaultTarget 必填。
 * 分支名/目标唯一性、defaultTarget 互异、表达式语法非白名单可表达规则，
 * 仍由手写校验保留（U36 双跑不比对 covered=false 项）。
 */
export const conditionSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    branches: {
      type: 'array',
      minItems: 1,
      items: {
        type: 'object',
        properties: {
          label: { type: 'string', pattern: '\\S' },
          expression: { type: 'string', pattern: '\\S', 'x-variable': true },
          target: {
            type: 'string',
            pattern: '\\S',
            'x-ref': { kinds: ['*'] },
            'x-widget': 'target-select',
          },
        },
        required: ['label', 'expression', 'target'],
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
      branch: {},
      target: {},
    },
  },
}
