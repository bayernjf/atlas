import type { NodeConfigSchema } from '../metaSchema'

/**
 * wait 节点 config schema（04 §5.5；event 分支 docs/47；duration dynamic docs/49；
 * duration absolute docs/50；投影自 nodeCatalog.ts 手写规则）。
 * duration static：1-600 整数秒；duration dynamic：durationExpression 运行时求值；
 * duration absolute：absoluteTime 运行时插值解析为目标时刻；
 * event：eventKey 静态模板、timeoutSeconds 1-3600、onTimeout。
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
        durationSeconds: { type: 'integer', minimum: 1, maximum: 600, default: 5 },
        durationExpression: { type: 'string', minLength: 1, maxLength: 200 },
        absoluteTime: { type: 'string', minLength: 1, maxLength: 64 },
      },
      required: ['waitType'],
    },
    {
      properties: {
        waitType: { type: 'string', const: 'event' },
        eventKey: { type: 'string', minLength: 1, maxLength: 128 },
        timeoutSeconds: { type: 'integer', minimum: 1, maximum: 3600 },
        onTimeout: { type: 'string', enum: ['continue', 'fail'], default: 'continue' },
      },
      required: ['waitType', 'eventKey', 'timeoutSeconds'],
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
      absoluteTime: { type: 'string' },
      eventKey: { type: 'string' },
      signaled: { type: 'boolean' },
      payload: { type: 'object' },
      waitedSeconds: { type: 'integer' },
      resolvedBy: { type: 'string' },
      token: { type: 'string' },
    },
  },
}
