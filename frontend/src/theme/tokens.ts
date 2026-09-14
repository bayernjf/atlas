/**
 * 设计 Token 单一事实源（17 文档 §3）。
 *
 * 三层模型：primitive（原始色板）→ semanticTokens（语义角色，唯一对外引用层）。
 * 消费方式：
 * - CSS：setup.ts 把 semanticTokens 注入为 `--atlas-*` 自定义属性，index.css 只引用变量；
 * - AntD：`antdTheme` 经 ConfigProvider 注入；
 * - JS/TS（React Flow 连线、节点目录色）：直接引用 semanticTokens。
 *
 * 暗色模式通过 [data-theme="dark"] 覆盖 semantic 层扩展（17 文档 §3.4，暂不实现）。
 * 2026-09-13 初版为现值 1:1 搬迁，零视觉变化。
 */

import type { ThemeConfig } from 'antd'

const primitive = {
  blue6: '#1677ff',
  green6: '#52c41a',
  purple6: '#722ed1',
  red5: '#ff4d4f',
  gold6: '#faad14',
  cyan6: '#08979c',
  magenta6: '#c41d7f',
  gray800: '#1f2937',
  gray200: '#e5e7eb',
  gray100: '#f5f7fa',
  slate200: '#e2e8f0',
  slate900: '#0f172a',
  canvasGray: '#eef2f7',
  white: '#ffffff',
} as const

export const semanticTokens = {
  // 品牌与状态
  'color-primary': primitive.blue6,
  'color-success': primitive.green6,
  'color-node-ai': primitive.purple6,
  'color-node-condition': primitive.gold6,
  'color-node-loop': primitive.cyan6,
  'color-node-parallel': primitive.magenta6,
  'color-danger': primitive.red5,
  // 文字
  'color-text-primary': primitive.gray800,
  'color-text-inverse': primitive.white,
  'color-text-on-dark': primitive.slate200,
  // 背景
  'color-bg-page': primitive.gray100,
  'color-bg-canvas': primitive.canvasGray,
  'color-bg-container': primitive.white,
  'color-bg-header': primitive.slate900,
  // 边框
  'color-border': primitive.gray200,
  // 节点运行态光晕（17 文档 §3.5：原 index.css rgba 现值 1:1 搬迁）
  'color-node-ring-running': 'rgba(22, 119, 255, 0.25)',
  'color-node-pulse-soft': 'rgba(22, 119, 255, 0.18)',
  'color-node-pulse-strong': 'rgba(22, 119, 255, 0.35)',
  'color-node-ring-completed': 'rgba(82, 196, 26, 0.35)',
  'color-status-pill-bg': 'rgba(255, 255, 255, 0.25)',
  // 阴影
  'shadow-node': 'rgba(0, 0, 0, 0.1)',
} as const

export type SemanticTokenName = keyof typeof semanticTokens

export function token(name: SemanticTokenName): string {
  return semanticTokens[name]
}

export const cssVarName = (name: SemanticTokenName): string => `--atlas-${name}`

export const antdTheme: ThemeConfig = {
  token: { colorPrimary: semanticTokens['color-primary'] },
}
