/**
 * 零依赖 i18n 文案骨架（docs/17 §2.3，M12；docs/57 全量翻译＋语言切换）。
 *
 * 本模块**不引入 i18next / react-i18next**（docs/57 §3 决策：当前无复数/懒加载
 * /Intl 需求，换库纯返工，D12 不解除），只用纯 TS + 已有的 react 提供一个接口
 * 签名与 i18next 对齐的最小实现：`t(key, options)` / `useTranslation(ns)`。
 *
 * docs/57 起 zh-CN/en-US 11 个 namespace 全量对齐；语言通过 `changeLanguage`
 * 切换并持久化到 localStorage（key `atlas.locale`），模块初始化时读回
 * （readStoredLocale）。**不做 navigator.language 自动探测**（种子客户定位中文，
 * 避免英文 navigator 误显，docs/57 §3）。AntD ConfigProvider locale 联动见
 * ./antdLocale.ts；后端结构化错误 detail 仍原样上屏、不 key 化。
 */
import { useCallback, useSyncExternalStore } from 'react'

import enCommon from './en-US/common.json'
import enApprovals from './en-US/approvals.json'
import enAudit from './en-US/audit.json'
import enChannels from './en-US/channels.json'
import enConnections from './en-US/connections.json'
import enDashboard from './en-US/dashboard.json'
import enDemo from './en-US/demo.json'
import enEditor from './en-US/editor.json'
import enMemory from './en-US/memory.json'
import enMonitoring from './en-US/monitoring.json'
import enOpenApi from './en-US/openapi.json'
import enRuntime from './en-US/runtime.json'
import enSchedules from './en-US/schedules.json'
import enValidation from './en-US/validation.json'
import enWaits from './en-US/waits.json'
import zhCommon from './zh-CN/common.json'
import zhApprovals from './zh-CN/approvals.json'
import zhAudit from './zh-CN/audit.json'
import zhChannels from './zh-CN/channels.json'
import zhConnections from './zh-CN/connections.json'
import zhDashboard from './zh-CN/dashboard.json'
import zhDemo from './zh-CN/demo.json'
import zhEditor from './zh-CN/editor.json'
import zhMemory from './zh-CN/memory.json'
import zhMonitoring from './zh-CN/monitoring.json'
import zhOpenApi from './zh-CN/openapi.json'
import zhRuntime from './zh-CN/runtime.json'
import zhSchedules from './zh-CN/schedules.json'
import zhValidation from './zh-CN/validation.json'
import zhWaits from './zh-CN/waits.json'

export type Locale = 'zh-CN' | 'en-US'
export type Namespace = 'common' | 'approvals' | 'audit' | 'editor' | 'dashboard' | 'demo' | 'monitoring' | 'memory' | 'connections' | 'channels' | 'openapi' | 'runtime' | 'schedules' | 'validation' | 'waits'

type Dict = Record<string, unknown>
export type TranslateOptions = {
  defaultValue?: string
  ns?: Namespace
  /** 显式指定取值语言；缺省用当前全局语言（docs/61 §2.4：memo 化渲染路径需避免隐式全局读）。 */
  lng?: Locale
} & Record<string, unknown>

const NAMESPACES: Namespace[] = ['common', 'approvals', 'audit', 'editor', 'dashboard', 'demo', 'monitoring', 'memory', 'connections', 'channels', 'openapi', 'runtime', 'schedules', 'validation', 'waits']

const resources: Record<Locale, Record<Namespace, Dict>> = {
  'zh-CN': {
    common: zhCommon as Dict,
    approvals: zhApprovals as Dict,
    audit: zhAudit as Dict,
    channels: zhChannels as Dict,
    connections: zhConnections as Dict,
    editor: zhEditor as Dict,
    dashboard: zhDashboard as Dict,
    demo: zhDemo as Dict,
    monitoring: zhMonitoring as Dict,
    memory: zhMemory as Dict,
    openapi: zhOpenApi as Dict,
    runtime: zhRuntime as Dict,
    schedules: zhSchedules as Dict,
    validation: zhValidation as Dict,
    waits: zhWaits as Dict,
  },
  'en-US': {
    common: enCommon as Dict,
    approvals: enApprovals as Dict,
    audit: enAudit as Dict,
    channels: enChannels as Dict,
    connections: enConnections as Dict,
    editor: enEditor as Dict,
    dashboard: enDashboard as Dict,
    demo: enDemo as Dict,
    monitoring: enMonitoring as Dict,
    memory: enMemory as Dict,
    openapi: enOpenApi as Dict,
    runtime: enRuntime as Dict,
    schedules: enSchedules as Dict,
    validation: enValidation as Dict,
    waits: enWaits as Dict,
  },
}

export const DEFAULT_LOCALE: Locale = 'zh-CN'
export const SUPPORTED_LOCALES: Locale[] = ['zh-CN', 'en-US']
export const LOCALE_STORAGE_KEY = 'atlas.locale'

type StorageLike = Pick<Storage, 'getItem'> | null | undefined

/**
 * 从存储读回上次选择的语言（docs/57 §4）：值为受支持的 locale 时原样返回，
 * 缺失/非法/存储不可用一律回退 DEFAULT_LOCALE。纯函数、可注入 storage 单测；
 * 不传 storage 时尝试全局 localStorage（SSR/隐私模式下安全回退）。
 * 刻意不读 navigator.language（docs/57 §3 决策）。
 */
export function readStoredLocale(storage?: StorageLike): Locale {
  let raw: string | null = null
  try {
    if (storage !== undefined) {
      raw = storage?.getItem(LOCALE_STORAGE_KEY) ?? null
    } else {
      raw = localStorage.getItem(LOCALE_STORAGE_KEY)
    }
  } catch {
    // 存储不可用（隐私模式/安全上下文/测试桩抛错）时回退默认语言
    raw = null
  }
  return (SUPPORTED_LOCALES as string[]).includes(raw ?? '') ? (raw as Locale) : DEFAULT_LOCALE
}

let language: Locale = readStoredLocale()
const listeners = new Set<() => void>()

export function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

function getLanguageSnapshot(): Locale {
  return language
}

function emitChange(): void {
  listeners.forEach((listener) => listener())
}

/**
 * 切换语言（docs/57 §4）：写内存状态并持久化到 localStorage，通知 hook 重渲染。
 * 无 localStorage（测试/隐私模式）时仅内存生效；非法/相同语言静默忽略。
 */
export function changeLanguage(next: Locale): void {
  if (!SUPPORTED_LOCALES.includes(next) || next === language) return
  language = next
  try {
    localStorage.setItem(LOCALE_STORAGE_KEY, next)
  } catch {
    // 无 localStorage（测试/隐私模式）时仅内存生效
  }
  emitChange()
}

export function getLanguage(): Locale {
  return language
}

function lookupPath(dict: Dict | undefined, segments: string[]): string | undefined {
  let current: unknown = dict
  for (const segment of segments) {
    if (current && typeof current === 'object' && segment in (current as Dict)) {
      current = (current as Dict)[segment]
    } else {
      return undefined
    }
  }
  return typeof current === 'string' ? current : undefined
}

/** 解析 `ns:key` 前缀；无前缀或前缀非已知 namespace 时回退调用方 ns。 */
function splitNamespace(key: string, fallback: Namespace): { ns: Namespace; path: string } {
  const colon = key.indexOf(':')
  if (colon > 0) {
    const prefix = key.slice(0, colon)
    if ((NAMESPACES as string[]).includes(prefix)) {
      return { ns: prefix as Namespace, path: key.slice(colon + 1) }
    }
  }
  return { ns: fallback, path: key }
}

function interpolate(template: string, vars?: Record<string, unknown>): string {
  if (!vars) return template
  return template.replace(/\{\{\s*([A-Za-z_$][\w$]*)\s*\}\}/g, (match, name: string) =>
    Object.prototype.hasOwnProperty.call(vars, name) ? String(vars[name]) : match,
  )
}

function resolve(lang: Locale, namespace: Namespace, key: string): string | undefined {
  const { ns, path } = splitNamespace(key, namespace)
  const segments = path.split('.')
  // 1. 当前语言、目标 namespace
  let hit = lookupPath(resources[lang][ns], segments)
  if (hit !== undefined) return hit
  // 2. 当前语言回退到 common（namespace 缺键时共享通用文案）
  if (ns !== 'common') {
    hit = lookupPath(resources[lang].common, segments)
    if (hit !== undefined) return hit
  }
  // 3. 回退到默认语言 zh-CN（en-US 个别缺键时仍显示中文，而非 key）
  if (lang !== DEFAULT_LOCALE) {
    hit = lookupPath(resources[DEFAULT_LOCALE][ns], segments)
    if (hit !== undefined) return hit
    if (ns !== 'common') {
      hit = lookupPath(resources[DEFAULT_LOCALE].common, segments)
      if (hit !== undefined) return hit
    }
  }
  return undefined
}

/**
 * 翻译函数（对齐 i18next `t(key, options)`）：
 * - 点路径嵌套 key（`auth.login.submit`），可带 `editor:` namespace 前缀；
 * - `{{var}}` 插值；缺插值变量保留原占位；
 * - 缺键依次回退 common namespace、默认语言，最终返回 key 本身（i18next 行为）；
 * - `options.defaultValue` 在缺键时优先于返回 key。
 */
export function t(key: string, options?: TranslateOptions): string {
  const namespace: Namespace = options?.ns ?? 'common'
  const value = resolve(options?.lng ?? language, namespace, key)
  if (value !== undefined) return interpolate(value, options)
  if (options?.defaultValue !== undefined) return interpolate(options.defaultValue, options)
  return key
}

export type UseTranslationResult = {
  t: (key: string, options?: Omit<TranslateOptions, 'ns'>) => string
  i18n: { language: Locale; changeLanguage: typeof changeLanguage }
}

/** React hook（对齐 react-i18next `useTranslation(ns)`），默认 common namespace。 */
export function useTranslation(namespace: Namespace = 'common'): UseTranslationResult {
  const lang = useSyncExternalStore(subscribe, getLanguageSnapshot, getLanguageSnapshot)
  const translate = useCallback(
    (key: string, options?: Omit<TranslateOptions, 'ns'>) => t(key, { ...options, ns: namespace }),
    [namespace],
  )
  return { t: translate, i18n: { language: lang, changeLanguage } }
}
