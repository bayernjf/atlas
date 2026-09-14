/**
 * 节点类型目录：默认配置与实时校验（04 §3.1-3.3，03 node_schema）。
 *
 * W5-W6 落 trigger / ai_decision / tool_call 三种（08 7.1）；
 * condition/loop/parallel 等类型随后续周次在同一目录注册。
 */

import { token } from '../theme/tokens'
import { validateExpression } from './conditions'

export const NODE_KINDS = ['trigger', 'ai_decision', 'tool_call', 'condition', 'loop', 'parallel', 'wait', 'subgraph', 'human_approval'] as const
export type NodeKind = (typeof NODE_KINDS)[number]

export const MAX_LOOP_ITERATIONS = 100
export const MIN_PARALLEL_BRANCHES = 2
export const MAX_PARALLEL_BRANCHES = 10
export const MIN_WAIT_SECONDS = 1
export const MAX_WAIT_SECONDS = 600
export const MIN_APPROVAL_TIMEOUT = 10
export const MAX_APPROVAL_TIMEOUT = 3600
export const APPROVAL_TIMEOUT_ACTIONS = ['approve', 'reject'] as const
export type ApprovalTimeoutAction = (typeof APPROVAL_TIMEOUT_ACTIONS)[number]
export const PARALLEL_JOIN_STRATEGIES = ['all_success', 'all_completed'] as const
export type ParallelJoinStrategy = (typeof PARALLEL_JOIN_STRATEGIES)[number]

export const ON_ERROR_STRATEGIES = ['stop', 'continue', 'jump_to'] as const
export type OnErrorStrategy = (typeof ON_ERROR_STRATEGIES)[number]

export type RetryConfig = {
  maxRetries: number
  backoff: string
  timeout: number
  onError: OnErrorStrategy
}

export type ConditionBranch = {
  label: string
  expression: string
  target: string
}

export type ParallelBranch = {
  label: string
  target: string
}

export type NodeConfig = {
  // trigger
  triggerType?: 'manual' | 'schedule' | 'webhook'
  cron?: string
  webhookUrl?: string
  // ai_decision
  promptTemplate?: string
  model?: string
  confidenceThreshold?: number
  // tool_call
  tool?: string
  params?: string
  // condition（04 §5.2；target 存在性/出边覆盖等图级校验由后端 422 兜底）
  branches?: ConditionBranch[] | ParallelBranch[]
  defaultTarget?: string
  // loop（04 §5.3；v1 仅 while 条件循环；回边/出边等图级校验由后端 422 兜底）
  mode?: 'while'
  continueExpression?: string
  maxIterations?: number
  bodyTarget?: string
  exitTarget?: string
  // parallel（04 §5.4；v1 静态扇出/汇聚；区域拓扑等图级校验由后端 422 兜底）
  joinStrategy?: ParallelJoinStrategy
  joinTarget?: string
  // wait（04 §5.5；v1 仅定时等待，事件等待缓做 docs/14 D19；出边等图级校验由后端 422 兜底）
  waitType?: 'duration'
  durationSeconds?: number
  // subgraph（04 §5.7；v1 引用已保存图，版本钉版缓做 docs/14 D21；出边等图级校验由后端 422 兜底）
  graphId?: string
  inputs?: Record<string, string>
  // human_approval（04 §5.6；v1 进程内审批信号，持久化中断缓做 docs/14 D20；出边等图级校验由后端 422 兜底）
  summary?: string
  approver?: string
  timeoutSeconds?: number
  onTimeout?: ApprovalTimeoutAction
  approvedTarget?: string
  rejectedTarget?: string
}

export type EditorNodeData = {
  label: string
  kind: NodeKind
  status: 'idle' | 'running' | 'completed'
  description?: string
  config: NodeConfig
  retry: RetryConfig
}

export const NODE_CATALOG: Record<NodeKind, { label: string; description: string; color: string }> = {
  trigger: { label: '触发器', description: '流程入口：定时 / Webhook / 手动', color: token('color-success') },
  ai_decision: { label: 'AI 决策', description: 'LLM 基于上下文判断下一步', color: token('color-node-ai') },
  tool_call: { label: '工具调用', description: '经 Harness 执行外部平台操作', color: token('color-primary') },
  condition: { label: '条件分支', description: '按规则表达式选择执行路径，默认分支必填', color: token('color-node-condition') },
  loop: { label: '循环', description: '条件为真时重复执行循环体，达最大次数自动退出', color: token('color-node-loop') },
  parallel: { label: '并行', description: '同时执行多个分支，汇聚后继续（全部成功/全部完成）', color: token('color-node-parallel') },
  wait: { label: '等待', description: '挂起指定时长后继续（1-600 秒）；事件等待暂不支持', color: token('color-node-wait') },
  subgraph: {
    label: '子图',
    description: '引用一张已保存的图作为子流程执行，可映射入参并引用其产出',
    color: token('color-node-subgraph'),
  },
  human_approval: {
    label: '人机协作',
    description: '暂停并请求人工审批，超时自动通过/拒绝（10-3600 秒）',
    color: token('color-node-human'),
  },
}

export function defaultConfig(kind: NodeKind): NodeConfig {
  switch (kind) {
    case 'trigger':
      return { triggerType: 'manual', cron: '', webhookUrl: '' }
    case 'ai_decision':
      return { promptTemplate: '', model: '', confidenceThreshold: 0.6 }
    case 'tool_call':
      return { tool: '', params: '' }
    case 'condition':
      return { branches: [{ label: '', expression: '', target: '' }], defaultTarget: '' }
    case 'loop':
      return {
        mode: 'while',
        continueExpression: '',
        maxIterations: 10,
        bodyTarget: '',
        exitTarget: '',
      }
    case 'parallel':
      return {
        joinStrategy: 'all_success',
        branches: [
          { label: '', target: '' },
          { label: '', target: '' },
        ],
        joinTarget: '',
      }
    case 'wait':
      return { waitType: 'duration', durationSeconds: 5 }
    case 'subgraph':
      return { graphId: '', inputs: {} }
    case 'human_approval':
      return {
        summary: '',
        approver: '',
        timeoutSeconds: 300,
        onTimeout: 'reject',
        approvedTarget: '',
        rejectedTarget: '',
      }
  }
}

export function defaultRetry(): RetryConfig {
  return { maxRetries: 0, backoff: '1s', timeout: 30, onError: 'stop' }
}

/**
 * 实时校验（04 §3.3：缺失必填项高亮提示）。返回错误消息数组，空数组表示通过。
 */
export function validateNode(data: EditorNodeData): string[] {
  const errors: string[] = []
  if (!data.label.trim()) errors.push('节点名称必填')

  const config = data.config
  switch (data.kind) {
    case 'trigger':
      if (config.triggerType === 'schedule' && !config.cron?.trim()) {
        errors.push('定时触发必须填写 Cron 表达式')
      }
      if (config.triggerType === 'webhook' && !config.webhookUrl?.trim()) {
        errors.push('Webhook 触发必须填写 URL')
      }
      break
    case 'ai_decision':
      if (!config.promptTemplate?.trim()) errors.push('AI 决策必须填写提示词模板')
      if (
        config.confidenceThreshold !== undefined &&
        (config.confidenceThreshold < 0 || config.confidenceThreshold > 1)
      ) {
        errors.push('置信度阈值需在 0-1 之间')
      }
      break
    case 'tool_call':
      if (!config.tool?.trim()) errors.push('工具调用必须选择工具')
      break
    case 'condition': {
      const branches = (config.branches ?? []) as ConditionBranch[]
      if (branches.length === 0) errors.push('条件节点至少需要一个分支')
      const labels = new Set<string>()
      const targets = new Set<string>()
      branches.forEach((branch, index) => {
        const tag = branch.label?.trim() || `第 ${index + 1} 个分支`
        if (!branch.label?.trim()) errors.push(`第 ${index + 1} 个分支名称不能为空`)
        else if (labels.has(branch.label)) errors.push(`分支名称重复：${branch.label}`)
        else labels.add(branch.label)
        if (!branch.expression?.trim()) {
          errors.push(`分支 ${tag} 的表达式不能为空`)
        } else {
          for (const exprError of validateExpression(branch.expression)) {
            errors.push(`分支 ${tag} ${exprError}`)
          }
        }
        if (!branch.target?.trim()) errors.push(`分支 ${tag} 必须选择目标节点`)
        else if (targets.has(branch.target)) errors.push(`分支目标重复：${branch.target}`)
        else targets.add(branch.target)
      })
      if (!config.defaultTarget?.trim()) errors.push('必须配置默认分支')
      else if (targets.has(config.defaultTarget)) errors.push('默认分支目标不能与其他分支相同')
      break
    }
    case 'loop': {
      if (!config.continueExpression?.trim()) {
        errors.push('必须填写继续条件表达式')
      } else {
        for (const exprError of validateExpression(config.continueExpression)) {
          errors.push(`继续条件表达式${exprError}`)
        }
      }
      const max = config.maxIterations
      if (max === undefined || !Number.isInteger(max) || max < 1 || max > MAX_LOOP_ITERATIONS) {
        errors.push(`最大次数需为 1-${MAX_LOOP_ITERATIONS} 的整数`)
      }
      if (!config.bodyTarget?.trim()) errors.push('必须选择循环体入口')
      if (!config.exitTarget?.trim()) errors.push('必须选择退出目标')
      if (config.bodyTarget && config.bodyTarget === config.exitTarget) {
        errors.push('循环体入口与退出目标不能相同')
      }
      break
    }
    case 'parallel': {
      const branches = (config.branches ?? []) as ParallelBranch[]
      if (branches.length < MIN_PARALLEL_BRANCHES || branches.length > MAX_PARALLEL_BRANCHES) {
        errors.push(`分支数需为 ${MIN_PARALLEL_BRANCHES}-${MAX_PARALLEL_BRANCHES} 个`)
      }
      const labels = new Set<string>()
      const targets = new Set<string>()
      branches.forEach((branch, index) => {
        const tag = branch.label?.trim() || `第 ${index + 1} 个分支`
        if (!branch.label?.trim()) errors.push(`第 ${index + 1} 个分支名称不能为空`)
        else if (labels.has(branch.label)) errors.push(`分支名称重复：${branch.label}`)
        else labels.add(branch.label)
        if (!branch.target?.trim()) errors.push(`分支 ${tag} 必须选择目标节点`)
        else if (targets.has(branch.target)) errors.push(`分支目标重复：${branch.target}`)
        else targets.add(branch.target)
      })
      if (!config.joinTarget?.trim()) {
        errors.push('必须选择汇聚目标')
      } else if (targets.has(config.joinTarget)) {
        errors.push('汇聚目标不能与任一分支目标相同')
      }
      break
    }
    case 'wait': {
      if (config.waitType !== 'duration') errors.push('等待类型必须为定时等待')
      const seconds = config.durationSeconds
      if (seconds === undefined || !Number.isInteger(seconds) || seconds < MIN_WAIT_SECONDS || seconds > MAX_WAIT_SECONDS) {
        errors.push(`等待时长需为 ${MIN_WAIT_SECONDS}-${MAX_WAIT_SECONDS} 秒的整数`)
      }
      break
    }
    case 'subgraph': {
      if (!config.graphId?.trim()) errors.push('必须选择引用的已保存子图')
      const keys = new Set<string>()
      Object.entries(config.inputs ?? {}).forEach(([key, value]) => {
        if (!key.trim()) errors.push('入参键名不能为空')
        else if (keys.has(key)) errors.push(`入参键名重复：${key}`)
        else keys.add(key)
        if (!value.trim()) errors.push(`入参 ${key || '(空键)'} 的映射值不能为空`)
      })
      break
    }
    case 'human_approval': {
      if (!config.summary?.trim()) errors.push('审批说明必填')
      const timeout = config.timeoutSeconds
      if (
        timeout === undefined ||
        !Number.isInteger(timeout) ||
        timeout < MIN_APPROVAL_TIMEOUT ||
        timeout > MAX_APPROVAL_TIMEOUT
      ) {
        errors.push(`超时时长需为 ${MIN_APPROVAL_TIMEOUT}-${MAX_APPROVAL_TIMEOUT} 秒的整数`)
      }
      if (config.onTimeout && !APPROVAL_TIMEOUT_ACTIONS.includes(config.onTimeout)) {
        errors.push('超时策略必须为自动通过或自动拒绝')
      }
      if (!config.approvedTarget?.trim()) errors.push('必须选择通过目标')
      if (!config.rejectedTarget?.trim()) errors.push('必须选择拒绝目标')
      if (
        config.approvedTarget &&
        config.approvedTarget === config.rejectedTarget
      ) {
        errors.push('通过目标与拒绝目标不能相同')
      }
      break
    }
  }
  return errors
}
