import type { NodeConfigSchema } from '../metaSchema'

/**
 * human_approval 节点 config schema（04 §5.6 / §4.9；投影自 nodeCatalog.ts 手写规则）。
 * summary 必填非空（接受 {{路径}}）；timeoutSeconds 10-3600 整数，默认 300；
 * onTimeout enum approve/reject，默认 reject（M4 x-widget radio 并排单选）；
 * approvedTarget/rejectedTarget 必填（M4 x-widget target-select 连线目标选择）。
 * 双 target 互异非白名单规则，手写保留。
 */
export const humanApprovalSchema: NodeConfigSchema = {
  type: 'object',
  properties: {
    summary: { type: 'string', pattern: '\\S', 'x-variable': true },
    approver: { type: 'string' },
    timeoutSeconds: { type: 'integer', minimum: 10, maximum: 3600, default: 300 },
    onTimeout: { type: 'string', enum: ['approve', 'reject'], default: 'reject', 'x-widget': 'radio' },
    approvedTarget: {
      type: 'string',
      pattern: '\\S',
      'x-ref': { kinds: ['*'] },
      'x-widget': 'target-select',
    },
    rejectedTarget: {
      type: 'string',
      pattern: '\\S',
      'x-ref': { kinds: ['*'] },
      'x-widget': 'target-select',
    },
    // M8：可选交互卡片 id；留空走 summary 旧路径，非空须命中内置卡片目录（后端编译期校验）。
    cardTemplateId: { type: 'string', 'x-widget': 'card-select' },
    // docs/35 §2（T2）：审批挂起通知邮箱，可选，最多 5 个；每项支持 {{路径}} 插值，
    // 后端编译期校验静态地址、运行时插值过滤空值/非法值；旧图无此字段行为不变。
    notifyEmails: {
      type: 'array',
      items: { type: 'string' },
      maxItems: 5,
      default: [],
    },
  },
  required: ['summary', 'timeoutSeconds', 'approvedTarget', 'rejectedTarget'],
  'x-outputSchema': {
    type: 'object',
    properties: {
      decision: {},
      target: {},
      summary: { type: 'string' },
      approver: { type: 'string' },
      resolvedBy: { type: 'string' },
      comment: { type: 'string' },
      card: {},
    },
  },
}
