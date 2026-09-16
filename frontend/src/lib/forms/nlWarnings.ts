/**
 * NL 草稿参数警告（M3，08 M3 立项条④ / 04 §4.10 校验与 NL 条）。
 *
 * `POST /api/nl/generate` 的 paramWarnings **仍是 string[]（wire 不变、后端零改动）**，
 * 文案由 `atlas.llm.nl_generate.validate_param_fills` 生成，形如
 * 「节点「query-1」工具 database/query 参数缺少必填字段：sql」——节点可定位、
 * 字段不可定位，因此表单化后按节点 id 归到该节点 params 根，落成
 * severity:'warning'、无 pointer 的非阻塞条目（FormRenderer 在根下汇总展示）。
 */
import type { Diagnostic } from '../validation/diagnostics'

export const NL_PARAM_WARNING_CODE = 'NL_PARAM_WARNING'

/** 后端文案的固定节点前缀。 */
export function nlWarningPrefix(nodeId: string): string {
  return `节点「${nodeId}」`
}

/** 该节点的 NL 参数警告原文（保持后端文案，不做改写）。 */
export function nlParamWarnings(warnings: string[] | undefined, nodeId: string): string[] {
  const prefix = nlWarningPrefix(nodeId)
  return (warnings ?? []).filter((warning) => warning.startsWith(prefix))
}

/** 该节点的 NL 参数警告 → 非阻塞诊断（无 pointer，挂 params 根）。 */
export function nlWarningDiagnostics(
  warnings: string[] | undefined,
  nodeId: string,
): Diagnostic[] {
  return nlParamWarnings(warnings, nodeId).map((message) => ({
    severity: 'warning' as const,
    layer: 'field' as const,
    code: NL_PARAM_WARNING_CODE,
    message,
    loc: {},
  }))
}
