/**
 * 节点类型目录：默认配置与实时校验（04 §3.1-3.3，03 node_schema）。
 *
 * W5-W6 落 trigger / ai_decision / tool_call 三种（08 7.1）；
 * condition/loop/parallel 等类型随后续周次在同一目录注册。
 */

import { token } from '../theme/tokens'

export const NODE_KINDS = ['trigger', 'ai_decision', 'tool_call'] as const
export type NodeKind = (typeof NODE_KINDS)[number]

export const ON_ERROR_STRATEGIES = ['stop', 'continue', 'jump_to'] as const
export type OnErrorStrategy = (typeof ON_ERROR_STRATEGIES)[number]

export type RetryConfig = {
  maxRetries: number
  backoff: string
  timeout: number
  onError: OnErrorStrategy
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
}

export function defaultConfig(kind: NodeKind): NodeConfig {
  switch (kind) {
    case 'trigger':
      return { triggerType: 'manual', cron: '', webhookUrl: '' }
    case 'ai_decision':
      return { promptTemplate: '', model: '', confidenceThreshold: 0.6 }
    case 'tool_call':
      return { tool: '', params: '' }
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
  }
  return errors
}
