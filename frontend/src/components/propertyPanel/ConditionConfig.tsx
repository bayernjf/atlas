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
  /** 整节点诊断；NodeConfigForm 内部只取 config 字段诊断（含表达式语法/分支唯一性）。 */
  diagnostics: Diagnostic[]
}

/**
 * condition 属性表单（M4 批 3 ⑬ 起由 FormRenderer schema 驱动）：
 * 动态 branches 数组（label/expression/target）与 defaultTarget 见
 * condition.schema.ts 与 nodeUiSchemas.conditionUiSchema；表达式语法、
 * 分支名/目标唯一、默认分支互异等跨字段规则仍由 L1 手写产出。
 */
export function ConditionConfig({ config, update, variablePaths, targetOptions, diagnostics }: Props) {
  return (
    <>
      <Typography.Text strong>条件分支（规则自上而下短路；LLM 模式由模型按描述选择，04 §5.2）</Typography.Text>
      <NodeConfigForm
        kind="condition"
        config={config}
        update={update}
        variablePaths={variablePaths}
        targetOptions={targetOptions}
        diagnostics={diagnostics}
      />
    </>
  )
}
