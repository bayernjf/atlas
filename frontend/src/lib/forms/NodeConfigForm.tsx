/**
 * NodeConfigForm：节点 config 的 schema 驱动表单共享包装（M4，04 §4.10 扩展）。
 *
 * 组装数据 schema（M1 SchemaRegistry）+ UISchema（nodeUiSchemas：中文文案/分组/
 * 条件显隐）+ 节点控件表（buildNodeRegistry，内置九件 + target-select）+ 拓扑
 * 作用域，把整节点 config 交给 FormRenderer。任何字段变更都产出下一整个 config。
 * 只透传带 pointer 的 config 字段诊断；无 pointer 的「节点名称必填」留在面板外层，
 * 不在 config 表单根下重复。
 */
import { useMemo, type ReactElement } from 'react'
import type { NodeConfig, NodeKind } from '../nodeCatalog'
import { schemaRegistry } from '../schemas'
import type { Diagnostic } from '../validation/diagnostics'
import { FormRenderer } from './FormRenderer'
import { buildNodeRegistry } from './nodeRegistry'
import { NODE_UI_SCHEMAS } from './nodeUiSchemas'
import type { WidgetTargetOption } from './types'

export type NodeConfigFormProps = {
  kind: NodeKind
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
  variablePaths: string[]
  targetOptions: WidgetTargetOption[]
  /** 整节点诊断（PropertyPanel 算好）；内部只取带 pointer 的 config 字段诊断。 */
  diagnostics: Diagnostic[]
}

export function NodeConfigForm({
  kind,
  config,
  update,
  variablePaths,
  targetOptions,
  diagnostics,
}: NodeConfigFormProps): ReactElement {
  const schema = schemaRegistry.get(kind)
  const uiSchema = NODE_UI_SCHEMAS[kind]
  const registry = useMemo(() => buildNodeRegistry(), [])
  const scope = useMemo(
    () => ({
      listPathsAt: () => variablePaths,
      listNodeTargets: () => targetOptions,
    }),
    [variablePaths, targetOptions],
  )
  const fieldDiagnostics = useMemo(
    () => diagnostics.filter((diagnostic) => !!diagnostic.loc.pointer),
    [diagnostics],
  )

  return (
    <FormRenderer
      schema={schema}
      value={config}
      onChange={(next) => update(next as Partial<NodeConfig>)}
      source="node"
      uiSchema={uiSchema}
      registry={registry}
      scope={scope}
      diagnostics={fieldDiagnostics}
    />
  )
}
