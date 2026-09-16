import type { NodeConfigSchema } from '../metaSchema'

/**
 * parallel 节点 config schema（04 §5.4 / §4.9；投影自 nodeCatalog.ts 手写规则）。
 * v1 静态扇出：2-10 个分支，每分支 label/target 非空，joinTarget 必填。
 * 分支名/目标唯一性与汇聚互异非白名单规则，手写保留；joinStrategy 的合法性
 * 不由 L1 手写规则检查（图级在 DSL L3），schema 仅声明 string。
 * x-outputSchema 仅含静态键；result 为动态入口（D30），不列键。
 */
export const parallelSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    joinStrategy: { type: 'string' },
    branches: {
      type: 'array',
      minItems: 2,
      maxItems: 10,
      items: {
        type: 'object',
        properties: {
          label: { type: 'string', pattern: '\\S' },
          target: { type: 'string', pattern: '\\S', 'x-ref': { kinds: ['*'] } },
        },
        required: ['label', 'target'],
      },
    },
    joinTarget: { type: 'string', pattern: '\\S', 'x-ref': { kinds: ['*'] } },
  },
  required: ['branches', 'joinTarget'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      status: {},
      branches: {},
      joinStrategy: { type: 'string' },
      joinTarget: {},
    },
  },
}
