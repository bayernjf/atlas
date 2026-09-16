/**
 * 字段高亮挂点（M2 预留，08 M2 立项条②）。
 *
 * M2 不渲染任何高亮；本函数仅把「某字段 pointer 上的 token 区间」从诊断列表中
 * 投影出来并锁定形状，供 M3 WidgetRegistry/FormRenderer 的模板输入框据此渲染
 * 高亮层（消费时保持诊断排序，不去重、不合并）。
 */

import type { Diagnostic, DiagnosticToken } from './diagnostics'

export function renderMarkers(diagnostics: Diagnostic[], pointer: string): DiagnosticToken[] {
  const markers: DiagnosticToken[] = []
  for (const diagnostic of diagnostics) {
    if (diagnostic.loc.pointer === pointer && diagnostic.loc.token) {
      markers.push(diagnostic.loc.token)
    }
  }
  return markers
}
