/**
 * 节点表单 UISchema 映射（M4，04 §4.10 扩展 / 03 `ui_schema`）。
 *
 * 数据 schema（lib/schemas，M1 单一事实源）只描述形状与校验；中文标题、占位、
 * 枚举文案、视觉分组/条件显隐这些设计态 UI 配置由本映射承接，随节点迁移逐个登记。
 * 只登记已切到 FormRenderer 的节点；未登记节点仍走旧手写组件。
 */
import type { NodeKind } from '../nodeCatalog'
import {
  MAX_APPROVAL_TIMEOUT,
  MAX_LOOP_ITERATIONS,
  MIN_APPROVAL_TIMEOUT,
} from '../validation/l1'
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

/**
 * trigger（04 §5.1）：hiddenWhen 的首个真实消费者。triggerType 决定显隐——
 * manual 无额外字段；schedule 显 cron；webhook 显 webhookUrl。必填由数据 schema
 * oneOf 三分支（M1 已解释）+ L1 承接，manual 不误报。
 */
export const triggerUiSchema: UiSchema = {
  labels: {
    triggerType: '触发方式',
    cron: 'Cron 表达式',
    webhookUrl: 'Webhook 路径',
  },
  placeholders: {
    cron: '0 9 * * *',
    webhookUrl: '/hooks/approval',
  },
  optionLabels: {
    triggerType: { manual: '手动触发', schedule: '定时（Cron）', webhook: 'Webhook' },
  },
  hiddenWhen: [
    { field: 'triggerType', equals: 'schedule', show: ['cron'] },
    { field: 'triggerType', equals: 'webhook', show: ['webhookUrl'] },
  ],
}

/**
 * loop（04 §5.3）：continueExpression 走 variable-input（schema x-variable，语法/
 * 非空/L2 引用由 L1 承接），maxIterations 走 number，body/exit 走 target-select。
 * mode 是 v1 内部字段（仅 while），静态隐藏；表达式语法红字经 diagnostics 落字段。
 */
export const loopUiSchema: UiSchema = {
  labels: {
    continueExpression:
      '继续条件（每轮重入时求值；体内可用 {{loop-x.index}} 引用当前轮次，从 1 开始）',
    maxIterations: `最大次数（达到后强制退出，1-${MAX_LOOP_ITERATIONS}）`,
    bodyTarget: '循环体入口（条件为真时进入；体内末端需连线回本节点）',
    exitTarget: '退出目标（条件为假 / 达上限 / 表达式异常时）',
  },
  placeholders: {
    continueExpression: '{{loop-1.index}} < 3',
    bodyTarget: '选择循环体入口节点',
    exitTarget: '选择退出目标节点',
  },
  hideFields: ['mode'],
}

/** 节点 kind → UISchema；未迁移节点缺省（FormRenderer 无 uiSchema 时退化为字段名直出）。 */
export const NODE_UI_SCHEMAS: Partial<Record<NodeKind, UiSchema>> = {
  trigger: triggerUiSchema,
  loop: loopUiSchema,
  human_approval: humanApprovalUiSchema,
}
