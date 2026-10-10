import type { NodeConfigSchema } from '../metaSchema'

/**
 * state_machine 节点 config schema（docs/123 §2.3，打包 AL）。
 * 业务对象按事件迁移状态：objectRef（对象引用，支持 {{路径}}）、event（触发事件）、
 * states（状态清单）+ initial（初态）、store（memory=跨 run 持久化 / run_globals=单 run），
 * transitions（事件驱动迁移表，from 支持 * 通配到 to）。
 * 非法迁移/状态唯一性等白名单无法表达规则，由后端编译期与运行期校验兜底。
 */
export const stateMachineSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    objectRef: { type: 'string', pattern: '\\S', 'x-variable': true },
    event: { type: 'string', pattern: '\\S' },
    states: {
      type: 'array',
      minItems: 1,
      items: { type: 'string', pattern: '\\S' },
    },
    initial: { type: 'string', pattern: '\\S' },
    store: {
      type: 'string',
      enum: ['memory', 'run_globals'],
      default: 'memory',
      'x-widget': 'radio',
    },
    transitions: {
      type: 'array',
      minItems: 0,
      items: {
        type: 'object',
        properties: {
          event: { type: 'string', pattern: '\\S' },
          from: { type: 'string' },
          to: { type: 'string', pattern: '\\S' },
          action: { type: 'string' },
        },
        required: ['event', 'from', 'to'],
      },
    },
  },
  required: ['objectRef', 'event', 'states', 'initial', 'store', 'transitions'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      object_ref: {},
      from_state: {},
      to_state: {},
      event: {},
    },
  },
}
