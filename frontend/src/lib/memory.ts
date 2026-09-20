/**
 * M11 长期记忆前端纯函数（docs/26 §7）：kind 标签/颜色、score 与字段格式化。
 * 抽离为无副作用纯函数便于 vitest 对拍。
 */
import type { MemoryKind } from './apiClient'

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
