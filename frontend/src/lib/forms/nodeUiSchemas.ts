/**
 * 节点表单 UISchema 映射（M4，04 §4.10 扩展 / 03 `ui_schema`）。
 *
 * 数据 schema（lib/schemas，M1 单一事实源）只描述形状与校验；中文标题、占位、
 * 枚举文案、视觉分组/条件显隐这些设计态 UI 配置由本映射承接，随节点迁移逐个登记。
 * 只登记已切到 FormRenderer 的节点；未登记节点仍走旧手写组件。
 */
import type { NodeKind } from '../nodeCatalog'
import { MAX_APPROVAL_TIMEOUT, MIN_APPROVAL_TIMEOUT } from '../validation/l1'
import type { UiSchema } from './uiSchema'

/**
 * human_approval（04 §5.6）：双目标并排（ui:group row）；onTimeout radio 中文案；
 * summary 走 variable-input（schema x-variable），双目标走 target-select。
 */
export const humanApprovalUiSchema: UiSchema = {
  labels: {
    summary: '审批说明（必填，支持 {{路径}} 引用）',
    approver: '审批人（可选，仅展示与审计，v1 不鉴权）',
    timeoutSeconds: `超时时长（${MIN_APPROVAL_TIMEOUT}-${MAX_APPROVAL_TIMEOUT} 秒）`,
    onTimeout: '超时策略（默认自动拒绝；超时后 run 仍完成）',
    approvedTarget: '通过目标（人工同意 / 超时自动通过时进入）',
    rejectedTarget: '拒绝目标（人工拒绝 / 超时自动拒绝时进入）',
  },
  placeholders: {
    summary:
      '订单 {{trigger-1.context.payload.order_id}} 退款 ¥{{trigger-1.context.payload.amount}}，请人工复核',
    approver: '客服主管',
    approvedTarget: '选择通过目标节点',
    rejectedTarget: '选择拒绝目标节点',
  },
  optionLabels: {
    onTimeout: { reject: '超时自动拒绝', approve: '超时自动通过' },
  },
  groups: [{ key: 'targets', fields: ['approvedTarget', 'rejectedTarget'], layout: 'row' }],
}

/** 节点 kind → UISchema；未迁移节点缺省（FormRenderer 无 uiSchema 时退化为字段名直出）。 */
export const NODE_UI_SCHEMAS: Partial<Record<NodeKind, UiSchema>> = {
  human_approval: humanApprovalUiSchema,
}
