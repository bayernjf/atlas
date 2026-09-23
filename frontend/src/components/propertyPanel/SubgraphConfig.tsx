import { Typography } from 'antd'
import { NodeConfigForm } from '../../lib/forms/NodeConfigForm'
import type { WidgetTargetOption } from '../../lib/forms/types'
import type { NodeConfig } from '../../lib/nodeCatalog'
import type { Diagnostic } from '../../lib/validation/diagnostics'
import { useTranslation } from '../../locales'

type Props = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
  variablePaths: string[]
  targetOptions: WidgetTargetOption[]
  /** 整节点诊断；NodeConfigForm 内部只取 config 字段诊断（含入参键重复）。 */
  diagnostics: Diagnostic[]
}

/**
 * subgraph 属性表单（M4 批 2 ⑨ 起由 FormRenderer schema 驱动）：
 * graphId 走 saved-graph-select（挂载拉 /api/graphs，空态/错误态由控件承接），
 * inputs 键值行走 variable-input；文案见 subgraph.schema.ts 与
 * nodeUiSchemas.subgraphUiSchema。空键名/键名重复仍由 L1 手写产出。
 */
export function SubgraphConfig({
  config,
  update,
  variablePaths,
  targetOptions,
  diagnostics,
}: Props) {
  const { t } = useTranslation('editor')
  return (
    <>
      <Typography.Text strong>
        {t('nodeTitles.subgraph')}
      </Typography.Text>
      <NodeConfigForm
        kind="subgraph"
        config={config}
        update={update}
        variablePaths={variablePaths}
        targetOptions={targetOptions}
        diagnostics={diagnostics}
      />
    </>
  )
}
