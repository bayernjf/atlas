/**
 * 节点类型目录：默认配置与实时校验（04 §3.1-3.3，03 node_schema）。
 *
 * W5-W6 落 trigger / ai_decision / tool_call 三种（08 7.1）；
 * condition/loop/parallel 等类型随后续周次在同一目录注册。
 */

import { token } from '../theme/tokens'
import type { CardBindings, JsonSchema, ScopeIndex } from './scope'
import type { ApprovalTimeoutAction, WaitTimeoutPolicy } from './validation/l1'

export {
  MAX_LOOP_ITERATIONS,
  MIN_PARALLEL_BRANCHES,
  MAX_PARALLEL_BRANCHES,
  MIN_WAIT_SECONDS,
  MAX_WAIT_SECONDS,
  MIN_EVENT_WAIT_SECONDS,
  MAX_EVENT_WAIT_SECONDS,
  MAX_EVENT_KEY_LENGTH,
  MAX_JITTER_SECONDS,
  MAX_EVENT_KEYS,
  MAX_DURATION_EXPRESSION_LENGTH,
  MAX_ABSOLUTE_TIME_LENGTH,
  WAIT_TIMEOUT_POLICIES,
  MIN_APPROVAL_TIMEOUT,
  MAX_APPROVAL_TIMEOUT,
  APPROVAL_TIMEOUT_ACTIONS,
} from './validation/l1'
export type { ApprovalTimeoutAction, WaitTimeoutPolicy } from './validation/l1'

export const NODE_KINDS = ['trigger', 'ai_decision', 'tool_call', 'condition', 'loop', 'parallel', 'wait', 'subgraph', 'human_approval'] as const
export type NodeKind = (typeof NODE_KINDS)[number]

export const PARALLEL_JOIN_STRATEGIES = ['all_success', 'all_completed', 'any_success'] as const
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
  expression?: string
  description?: string
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
  conditionMode?: 'rule' | 'llm'
  classifierPrompt?: string
  // loop（04 §5.3；while 条件循环 / foreach 遍历循环；回边/出边等图级校验由后端 422 兜底）
  mode?: 'while' | 'foreach'
  continueExpression?: string
  maxIterations?: number
  itemsExpression?: string
  itemName?: string
  collectTarget?: string
  bodyTarget?: string
  exitTarget?: string
  // parallel（04 §5.4；v1 静态扇出/汇聚；区域拓扑等图级校验由后端 422 兜底）
  joinStrategy?: ParallelJoinStrategy
  joinTarget?: string
  // wait（04 §5.5；event 进程内 v1 见 docs/47；出边等图级校验由后端 422 兜底）
  waitType?: 'duration' | 'event'
  durationMode?: 'static' | 'dynamic' | 'absolute'
  durationSeconds?: number
  durationExpression?: string
  absoluteTime?: string
  jitterSeconds?: number // docs/54：duration static 抖动上限 0-300
  eventKey?: string
  eventKeys?: string[] // docs/54：多事件 OR 竞速 1-8 个标识（与 eventKey 互斥）
  eventWaitMode?: 'any' | 'all' // docs/55：any=OR 首决（默认），all=AND 全命中（≥2 键）
  timeoutMode?: 'static' | 'expression'
  timeoutExpression?: string
  // subgraph（04 §5.7；v1 引用已保存图，版本钉版缓做 docs/14 D21；出边等图级校验由后端 422 兜底）
  graphId?: string
  inputs?: Record<string, string>
  // human_approval（04 §5.6；v1 进程内审批信号，持久化中断缓做 docs/14 D20；出边等图级校验由后端 422 兜底）
  summary?: string
  approver?: string
  timeoutSeconds?: number
  /** wait event 用 continue/fail；human_approval 用 approve/reject。 */
  onTimeout?: ApprovalTimeoutAction | WaitTimeoutPolicy
  approvedTarget?: string
  rejectedTarget?: string
  /** M8：可选内置交互卡片 id；留空走 summary 旧路径。 */
  cardTemplateId?: string
}

export type EditorNodeData = {
  label: string
  kind: NodeKind
  status: 'idle' | 'running' | 'completed' | 'paused'
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
  wait: { label: '等待', description: '定时等待（1-3600 秒，可加抖动）或等待一个/多个外部事件信号（最长 24 小时）', color: token('color-node-wait') },
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
 * L2 跨节点模板引用校验上下文（04 §6.5）；结构化校验由
 * lib/validation/validateGraph 的 validateNodeDiagnostics 统一聚合。
 */
export type RefValidationContext = {
  selfId: string
  scope: ScopeIndex
  toolOutputSchemas?: Record<string, JsonSchema>
  /** D30：工具入参 schema 表，供 REF_TYPE_MISMATCH 类型比对的期望类型。 */
  toolInputSchemas?: Record<string, JsonSchema>
  /** M8：卡片 id → 只读 binding 模板串，供审批卡 bindings 的 L2 校验。 */
  cardBindings?: CardBindings
}
