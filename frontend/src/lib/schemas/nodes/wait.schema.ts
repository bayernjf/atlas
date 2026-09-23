import type { NodeConfigSchema } from '../metaSchema'

/**
 * wait 节点 config schema（04 §5.5；event 分支 docs/47；duration dynamic docs/49；
 * duration absolute docs/50；上限放开/jitter/多事件竞速 docs/54；投影自 nodeCatalog.ts 手写规则）。
 * duration static：1-3600 整数秒，可选 jitterSeconds（0-300 抖动上限）；
 * duration dynamic：durationExpression 运行时求值；
 * duration absolute：absoluteTime 运行时插值解析为目标时刻；
 * event：单键 eventKey 或多键 eventKeys（1-8，互斥，二选一由 L1 校验）、
 * timeoutMode（static 用 timeoutSeconds 1-86400 / expression 用 timeoutExpression 运行时求值）、onTimeout。
 */
export const waitSchema: NodeConfigSchema = {
  type: 'object',
  oneOf: [
    {
      properties: {
        waitType: { type: 'string', const: 'duration', default: 'duration' },
        durationMode: {
          type: 'string',
          enum: ['static', 'dynamic', 'absolute'],
          default: 'static',
          'x-widget': 'radio',
        },
        durationSeconds: { type: 'integer', minimum: 1, maximum: 3600, default: 5 },
        jitterSeconds: { type: 'integer', minimum: 0, maximum: 300 },
        durationExpression: { type: 'string', minLength: 1, maxLength: 200 },
        absoluteTime: { type: 'string', minLength: 1, maxLength: 64 },
      },
      required: ['waitType'],
    },
    {
      properties: {
        waitType: { type: 'string', const: 'event' },
        eventKey: { type: 'string', minLength: 1, maxLength: 128 },
        eventKeys: {
          type: 'array',
          minItems: 1,
          maxItems: 8,
          items: { type: 'string', minLength: 1, maxLength: 128 },
        },
        timeoutMode: {
          type: 'string',
          enum: ['static', 'expression'],
          default: 'static',
          'x-widget': 'radio',
        },
        timeoutSeconds: { type: 'integer', minimum: 1, maximum: 86400 },
        timeoutExpression: { type: 'string', minLength: 1, maxLength: 200 },
        onTimeout: { type: 'string', enum: ['continue', 'fail'], default: 'continue' },
      },
      required: ['waitType'],
    },
  ],
  'x-outputSchema': {
    type: 'object',
    properties: {
      mode: {},
      waitType: { type: 'string' },
      durationMode: { type: 'string' },
      durationExpression: { type: 'string' },
      durationSeconds: { type: 'integer' },
      plannedDurationSeconds: { type: 'integer' },
      jitterSeconds: { type: 'integer' },
      absoluteTime: { type: 'string' },
      eventKey: { type: 'string' },
      eventKeys: { type: 'array', items: { type: 'string' } },
      matchedEventKey: { type: 'string' },
      signaled: { type: 'boolean' },
      payload: { type: 'object' },
      waitedSeconds: { type: 'integer' },
      resolvedBy: { type: 'string' },
      token: { type: 'string' },
    },
  },
}
