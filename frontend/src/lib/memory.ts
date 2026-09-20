/**
 * M11 长期记忆前端纯函数（docs/26 §7）：kind 标签/颜色、score 与字段格式化。
 * 抽离为无副作用纯函数便于 vitest 对拍。
 */
import type { MemoryKind, MemoryWritePayload } from './apiClient'

export const MEMORY_KIND_LABELS: Record<MemoryKind, string> = {
  fact: '事实',
  preference: '偏好',
}

/** fact 蓝 / preference 紫（docs/26 §7）。 */
export const MEMORY_KIND_COLORS: Record<MemoryKind, string> = {
  fact: 'blue',
  preference: 'purple',
}

export function kindLabel(kind: MemoryKind): string {
  return MEMORY_KIND_LABELS[kind] ?? kind
}

export function kindColor(kind: MemoryKind): string {
  return MEMORY_KIND_COLORS[kind] ?? 'default'
}

/** 余弦相似度 0–1 → 百分比整数。 */
export function formatScore(score: number): string {
  const clamped = Math.max(0, Math.min(1, score))
  return `${Math.round(clamped * 100)}%`
}

/** 置信度保留两位小数展示。 */
export function formatConfidence(confidence: number): string {
  return Number(confidence).toFixed(2)
}

/** scope 键值对扁平为 `k=v` 逗号串；空 scope 返空串。 */
export function formatScope(scope: Record<string, string> | null | undefined): string {
  if (!scope) return ''
  return Object.entries(scope)
    .map(([key, value]) => `${key}=${value}`)
    .join('，')
}

/** UTC ISO → 本地「YYYY-MM-DD HH:mm:ss」；非法时原样返回。 */
export function formatCreatedAt(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const pad = (n: number) => String(n).padStart(2, '0')
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`
  )
}


/**
 * docs/28 §5.1 ⑩：记忆新建/编辑表单纯函数（JSON 文本域 → 扁平 string→string map）。
 * 抽离为无副作用纯函数便于 vitest；解析失败给中文错误，供 UI 禁用提交。
 */
export type MemoryDraft = {
  kind: MemoryKind
  content: string
  confidence: number
  scopeText: string
  metadataText: string
}

export type StringMapResult =
  | { ok: true; value: Record<string, string> }
  | { ok: false; error: string }

/** 空文本视为 {}；否则必须是键值均为字符串的 JSON 对象（数组/原始值拒）。 */
export function parseStringMapText(text: string): StringMapResult {
  const trimmed = text.trim()
  if (!trimmed) return { ok: true, value: {} }
  let parsed: unknown
  try {
    parsed = JSON.parse(trimmed)
  } catch {
    return { ok: false, error: '必须是合法 JSON 对象' }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { ok: false, error: '必须是 JSON 对象（键值均为字符串）' }
  }
  for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
    if (!key || typeof value !== 'string') {
      return { ok: false, error: '键和值都必须是非空字符串' }
    }
  }
  return { ok: true, value: parsed as Record<string, string> }
}

/** 校验表单并构造后端 payload；content/confidence/JSON 任一非法返中文 error。 */
export function buildMemoryPayload(draft: MemoryDraft): {
  payload?: MemoryWritePayload
  error?: string
} {
  const content = draft.content.trim()
  if (!content) return { error: '内容不能为空' }
  if (content.length > 2000) return { error: '内容最长 2000 字' }
  if (
    typeof draft.confidence !== 'number' ||
    Number.isNaN(draft.confidence) ||
    draft.confidence < 0 ||
    draft.confidence > 1
  ) {
    return { error: '置信度必须是 0-1 之间的数' }
  }
  const scope = parseStringMapText(draft.scopeText)
  if (!scope.ok) return { error: `作用域${scope.error}` }
  const metadata = parseStringMapText(draft.metadataText)
  if (!metadata.ok) return { error: `元数据${metadata.error}` }
  return {
    payload: {
      kind: draft.kind,
      content,
      confidence: draft.confidence,
      scope: scope.value,
      metadata: metadata.value,
    },
  }
}
