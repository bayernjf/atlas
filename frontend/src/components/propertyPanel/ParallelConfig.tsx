import { Typography } from 'antd'
import { NodeConfigForm } from '../../lib/forms/NodeConfigForm'
import type { WidgetTargetOption } from '../../lib/forms/types'
import type { NodeConfig } from '../../lib/nodeCatalog'
import type { Diagnostic } from '../../lib/validation/diagnostics'

type Props = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
  targetOptions: WidgetTargetOption[]
  /** 整节点诊断；NodeConfigForm 内部只取 config 字段诊断（含分支数/汇聚互异）。 */
  diagnostics: Diagnostic[]
}

/**
 * parallel 属性表单（M4 批 2 ⑨ 起由 FormRenderer schema 驱动）：
 * 字段/文案/数组门控见 parallel.schema.ts 与 nodeUiSchemas.parallelUiSchema；
 * 分支名/目标重复、汇聚互异等跨字段规则仍由 L1 手写产出。
 */
export function ParallelConfig({ config, update, targetOptions, diagnostics }: Props) {
  return (
    <>
      <Typography.Text strong>
        并行扇出（同时执行各分支后汇聚，04 §5.4）
      </Typography.Text>
      <NodeConfigForm
        kind="parallel"
        config={config}
        update={update}
        variablePaths={[]}
        targetOptions={targetOptions}
        diagnostics={diagnostics}
      />
    </>
  )
}
