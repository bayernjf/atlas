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
    model: { type: 'string' },
    // 打包 AE（docs/111 §2.2）：LLM 分支置信度阈值，0–1；缺省不启用。
    confidenceThreshold: { type: 'number', minimum: 0, maximum: 1 },
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
      // 打包 AP（docs/14 D66 闭合）：这两个键 loader 一直在产出（rule/llm 两档都出
      // evaluation，llm 档还出 llm_errors），声明里却漏了 ⇒ 与校验表同为超集/子集漂移。
      evaluation: { type: 'array' },
      llm_errors: { type: 'array' },
      // 打包 BI（docs/14 D54 闭合）：与 loop.schema.ts 同款三数组（按下标对齐）。
      expression_errors: { type: 'array' },
      expressionErrorCodes: { type: 'array' },
      expressionErrorParams: { type: 'array' },
    },
  },
}
