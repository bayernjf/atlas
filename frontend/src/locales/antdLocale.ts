/**
 * AntD ConfigProvider locale 与自研 i18n 运行时联动（docs/57 §4）。
 *
 * 独立成文件、静态 import antd locale 包，使 locales/index.ts 保持零 UI 库依赖；
 * App 外壳调用一次 useAntdLocale() 并注入所有 ConfigProvider，切换语言时
 * useSyncExternalStore 驱动同步重渲染。
 *
 * **日期类组件的坑（docs/61 打包 H）**：AntD DatePicker/RangePicker 的面板月名与星期名
 * 取自 **dayjs 全局 locale**，不受此处 ConfigProvider 的 antd locale 控制。dayjs 目前只是
 * antd 的传递依赖，在 pnpm 隔离布局下**无法从本项目解析**（只存在于 `.pnpm/dayjs@*`），
 * 因此引日期组件前必须先把 dayjs 提为**声明的直接依赖**并配 locale（属选型决策，需走 docs/10），
 * 否则中文界面会渲染出 `2026年Sep` / `Su Mo Tu`。审计页即因此改用原生 `datetime-local`。
 */
import { useSyncExternalStore } from 'react'

import type { Locale as AntdLocale } from 'antd/es/locale'
import enUS from 'antd/locale/en_US'
import zhCN from 'antd/locale/zh_CN'

import { getLanguage, subscribe, type Locale } from './index'

const ANTD_LOCALES: Record<Locale, AntdLocale> = {
  'zh-CN': zhCN as AntdLocale,
  'en-US': enUS as AntdLocale,
}

/** 返回当前语言对应的 AntD locale 对象，语言切换时自动更新。 */
export function useAntdLocale(): AntdLocale {
  const lang = useSyncExternalStore(subscribe, getLanguage, getLanguage)
  return ANTD_LOCALES[lang]
}
