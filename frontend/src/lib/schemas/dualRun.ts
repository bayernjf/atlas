/**
 * 手写 L1 与 schema 派生 L1 的 dev 模式双跑比对（U36，04 §4.9）。
 * 只比对 schema 可表达的字段结论（covered=true 的手写规则）；表达式语法、
 * 分支唯一性、跨字段互异等非子集规则不参与 M1 双跑。
 */

import { schemaRegistry } from './index'
import { validateConfigBySchema } from './validateConfig'

export type HandL1Error = {
  path: string
  /** 该手写规则是否属于 schema 白名单可表达范围。 */
  covered: boolean
}

export type L1Divergence = {
  kind: string
  schemaOnly: string[]
  handOnly: string[]
}

export function diffNodeL1(
  kind: string,
  config: unknown,
  handErrors: HandL1Error[],
): L1Divergence | null {
  if (!schemaRegistry.registeredKinds().includes(kind)) return null
  const schemaPaths = new Set(validateConfigBySchema(schemaRegistry.get(kind), config).map((d) => d.path))
  const handPaths = new Set(handErrors.filter((error) => error.covered).map((error) => error.path))
  const schemaOnly = [...schemaPaths].filter((path) => !handPaths.has(path)).sort()
  const handOnly = [...handPaths].filter((path) => !schemaPaths.has(path)).sort()
  if (schemaOnly.length === 0 && handOnly.length === 0) return null
  return { kind, schemaOnly, handOnly }
}
