/**
 * D26 影子模式前端纯函数（docs/33 §3、§7）。
 *
 * 标签一律返回 monitoring namespace 下的 i18n key、由组件 t() 解析（照 lib/monitoring.ts
 * RULE_LABELS 先例，纯函数不引 hook）；技术枚举（permission 串、SHADOW_DRY_RUN 原值、
 * 未知动作）原样保留，不强行翻译。
 */
import type { ShadowRun, ToolIntent } from './apiClient'

/** 人工标准动作候选（后端 normalize_human_action 识别的退款/转人工别名之标准值）。 */
export const HUMAN_ACTION_VALUES = ['refunded', 'human_review'] as const
export type HumanActionValue = (typeof HUMAN_ACTION_VALUES)[number]

/**
 * 把 TextArea 文本解析为影子运行 inputs 顶层 JSON 对象。
 * 空白 → 空对象；非法 JSON 或非对象 → ok:false + monitoring namespace 的 i18n key。
 */
export function parseShadowInputs(
  text: string,
): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  const trimmed = text.trim()
  if (!trimmed) return { ok: true, value: {} }
  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    return { ok: false, error: 'shadow.error.inputsInvalidJson' }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { ok: false, error: 'shadow.error.inputsNotObject' }
  }
  return { ok: true, value: parsed as Record<string, unknown> }
}

/** 对比结论三态：一致 / 不一致 / 待判定（系统无写意图或尚无人工结果）。 */
export type ComparisonVerdict = 'consistent' | 'mismatch' | 'pending'

export function comparisonVerdict(match: boolean | null): ComparisonVerdict {
  if (match === true) return 'consistent'
  if (match === false) return 'mismatch'
  return 'pending'
}

/** 工具意图的展示种类（决定 Tag 颜色与中文标签 key）。 */
export type IntentKind = 'dryRun' | 'passThrough' | 'simulated' | 'failed'

export function intentKind(intent: ToolIntent): IntentKind {
  if (intent.action_status === 'SHADOW_DRY_RUN' || intent.dry_run) return 'dryRun'
  if (intent.action_status === 'SIMULATED') return 'simulated'
  if (intent.action_status === 'FAILED') return 'failed'
  return 'passThrough'
}

export const INTENT_KIND_COLORS: Record<IntentKind, string> = {
  dryRun: 'orange',
  passThrough: 'blue',
  simulated: 'default',
  failed: 'red',
}

/** 系统自动动作 → monitoring namespace i18n key；未知动作原样返回（缺键/原值都原样显示）。 */
export function autoActionLabel(action: string | null): string | null {
  if (action === null) return null
  if (action === 'refunded') return 'shadow.action.refunded'
  if (action === 'human_review') return 'shadow.action.humanReview'
  return action
}

/** 工具参数 JSON 展示（技术数据不译；空参数显破折号由组件处理，这里返 null）。 */
export function formatParameters(parameters: Record<string, unknown> | null): string | null {
  if (!parameters || Object.keys(parameters).length === 0) return null
  return JSON.stringify(parameters, null, 2)
}

/** 该影子运行是否含可对比的写意图（无自动动作时对比永远 pending）。 */
export function hasAutoAction(run: ShadowRun): boolean {
  return run.auto_action !== null
}
