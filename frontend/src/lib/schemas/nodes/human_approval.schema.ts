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
    },
  },
}
