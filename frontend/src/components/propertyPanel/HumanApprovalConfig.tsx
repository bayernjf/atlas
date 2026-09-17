import { Typography } from 'antd'
import { NodeConfigForm } from '../../lib/forms/NodeConfigForm'
import type { WidgetTargetOption } from '../../lib/forms/types'
import type { NodeConfig } from '../../lib/nodeCatalog'
import type { Diagnostic } from '../../lib/validation/diagnostics'

type Props = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
  variablePaths: string[]
  targetOptions: WidgetTargetOption[]
  /** 整节点诊断（PropertyPanel 算好）；NodeConfigForm 内部只取 config 字段诊断。 */
  diagnostics: Diagnostic[]
}

/**
 * human_approval 属性表单（M4 起由 FormRenderer schema 驱动）：
 * 字段/中文文案/分组见 human_approval.schema.ts 与 nodeUiSchemas.humanApprovalUiSchema；
 * 双 target 互异等跨字段校验仍由 L1 手写规则产出（pointer 落 /approvedTarget）。
 */
export function HumanApprovalConfig({
  config,
  update,
  variablePaths,
  targetOptions,
  diagnostics,
}: Props) {
  return (
    <>
      <Typography.Text strong>人机协作（暂停等待人工审批，04 §5.6）</Typography.Text>
      <NodeConfigForm
        kind="human_approval"
        config={config}
        update={update}
        variablePaths={variablePaths}
        targetOptions={targetOptions}
        diagnostics={diagnostics}
      />
    </>
  )
}
