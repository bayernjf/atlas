/**
 * 把 semantic tokens 注入 :root 为 `--atlas-*` CSS 自定义属性（17 文档 §3.2）。
 * 在 main.tsx 中 import 一次；index.css 只引用变量，不再出现硬编码色值。
 */

import { cssVarName, semanticTokens } from './tokens'

const root = document.documentElement
for (const [name, value] of Object.entries(semanticTokens)) {
  root.style.setProperty(cssVarName(name as keyof typeof semanticTokens), value)
}
