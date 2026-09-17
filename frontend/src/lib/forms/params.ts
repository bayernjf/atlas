/**
 * tool_call 节点 params 的存储形态（M3，08 M3 立项条③ / 04 §4.10）。
 *
 * `config.params` 永远是 **JSON 字符串**：EdgeDSL/Graph v1、保存/编译/运行与
 * 运行期整串 `{{路径}}` 插值语义全部不变，后端零改动。表单化只是编辑期形态：
 * 文本 → 解析为对象供 FormRenderer 渲染，变更 → 重新 stringify 回写。
 * 不可解析的文本（典型为整串含裸 `{{}}` 插值的旧参数）不做部分解析，停留旧
 * JSON 文本框。
 */

import { isPlainObject } from './formTree'

/**
 * params 文本 → 对象。
 * - 空/空白文本 → `{}`（新建节点尚无参数的初始态）
 * - 可解析且为对象 → 该对象
 * - 解析失败或结果非对象（数组/标量/裸 `{{}}` 串）→ `null`（调用方停留 JSON 文本框）
 */
export function parseParamsObject(text: string | undefined): Record<string, unknown> | null {
  const raw = (text ?? '').trim()
  if (raw === '') return {}
  try {
    const parsed: unknown = JSON.parse(raw)
    return isPlainObject(parsed) ? parsed : null
  } catch {
    return null
  }
}

/** 编辑结果 → params 字符串（回写通道，保持 JSON 字符串存储不变）。 */
export function paramsToText(value: unknown): string {
  if (value === undefined) return ''
  return JSON.stringify(value)
}
