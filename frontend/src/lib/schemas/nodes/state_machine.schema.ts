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
  // 打包 AN②（闭合 D64）：键集合逐字取自后端 `graph/loader.py` 的 state_machine 产出
  // （`{mode, objectRef, from, to, event, state, states, action}`，camelCase 与 docs/123
  // §2.3 的 config 与 `state_changed` SSE 帧同族）。原先声明的是 `{object_ref, from_state,
  // to_state, event}` 四个运行期永远解析不到的路径——画布变量补全会照着它给出空值。
  'x-outputSchema': {
    type: 'object',
    properties: {
      mode: {},
      objectRef: {},
      from: {},
      to: {},
      event: {},
      state: {},
      states: {},
      action: {},
    },
  },
}
