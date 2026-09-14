/**
 * 节点类型目录：默认配置与实时校验（04 §3.1-3.3，03 node_schema）。
 *
 * W5-W6 落 trigger / ai_decision / tool_call 三种（08 7.1）；
 * condition/loop/parallel 等类型随后续周次在同一目录注册。
 */

import { token } from '../theme/tokens'
import { validateExpression } from './conditions'

export const NODE_KINDS = ['trigger', 'ai_decision', 'tool_call', 'condition', 'loop'] as const
export type NodeKind = (typeof NODE_KINDS)[number]

export const MAX_LOOP_ITERATIONS = 100

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
  branches?: ConditionBranch[]
  defaultTarget?: string
  // loop（04 §5.3；v1 仅 while 条件循环；回边/出边等图级校验由后端 422 兜底）
  mode?: 'while'
  continueExpression?: string
  maxIterations?: number
  bodyTarget?: string
  exitTarget?: string
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
      const branches = config.branches ?? []
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
  }
  return errors
}
