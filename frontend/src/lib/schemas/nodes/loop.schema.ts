import type { NodeConfigSchema } from '../metaSchema'

/**
 * loop 节点 config schema（04 §5.3 / §4.9；投影自 nodeCatalog.ts 手写规则）。
 * v1 仅 while：continueExpression 非空（接受 {{路径}}），maxIterations 1-100 整数，
 * bodyTarget/exitTarget 必填。表达式语法与 body/exit 互异非白名单规则，手写保留。
 */
export const loopSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    mode: { type: 'string' },
    continueExpression: { type: 'string', pattern: '\\S', 'x-variable': true },
    maxIterations: { type: 'integer', minimum: 1, maximum: 100, default: 10 },
    bodyTarget: { type: 'string', pattern: '\\S', 'x-ref': { kinds: ['*'] } },
    exitTarget: { type: 'string', pattern: '\\S', 'x-ref': { kinds: ['*'] } },
  },
  required: ['continueExpression', 'maxIterations', 'bodyTarget', 'exitTarget'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      index: { type: 'integer' },
      iterations: { type: 'integer' },
    },
  },
}
