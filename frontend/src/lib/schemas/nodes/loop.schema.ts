import type { NodeConfigSchema } from '../metaSchema'

/**
 * loop 节点 config schema（04 §5.3 / §4.9；投影自 nodeCatalog.ts 手写规则）。
 * while：continueExpression 非空（接受 {{路径}}），maxIterations 1-100 整数；
 * foreach（docs/45）：itemsExpression 非空（首轮冻结的数组表达式）。
 * bodyTarget/exitTarget 两模式均必填；表达式语法与 body/exit 互异等规则手写保留。
 */
export const loopSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    mode: { type: 'string', enum: ['while', 'foreach'], default: 'while' },
    continueExpression: { type: 'string', pattern: '\\S', 'x-variable': true },
    maxIterations: { type: 'integer', minimum: 1, maximum: 100, default: 10 },
    itemsExpression: { type: 'string', pattern: '\\S', 'x-variable': true },
    itemName: { type: 'string' },
    collectTarget: { type: 'string', 'x-ref': { kinds: ['*'] }, 'x-widget': 'target-select' },
    bodyTarget: { type: 'string', pattern: '\\S', 'x-ref': { kinds: ['*'] }, 'x-widget': 'target-select' },
    exitTarget: { type: 'string', pattern: '\\S', 'x-ref': { kinds: ['*'] }, 'x-widget': 'target-select' },
  },
  oneOf: [
    {
      properties: {
        mode: { const: 'while' },
        continueExpression: { type: 'string', pattern: '\\S' },
        maxIterations: { type: 'integer', minimum: 1, maximum: 100 },
      },
      required: ['continueExpression', 'maxIterations'],
    },
    {
      properties: {
        mode: { const: 'foreach' },
        itemsExpression: { type: 'string', pattern: '\\S' },
      },
      required: ['itemsExpression'],
    },
  ],
  required: ['bodyTarget', 'exitTarget'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      mode: { type: 'string' },
      index: { type: 'integer' },
      iterations: { type: 'integer' },
      items: { type: 'array' },
      item: {},
      results: { type: 'array' },
      target: { type: 'string' },
      exitReason: { type: 'string' },
      expression_errors: { type: 'array' },
      expressionErrorCodes: { type: 'array' },
    },
  },
}
