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
  /** 整节点诊断；NodeConfigForm 内部只取 config 字段诊断（含表达式语法 /continueExpression）。 */
  diagnostics: Diagnostic[]
}

/**
 * loop 属性表单（M4 起由 FormRenderer schema 驱动）：
 * 字段/文案/隐藏见 loop.schema.ts 与 nodeUiSchemas.loopUiSchema；
 * 表达式语法、body/exit 互异等跨字段规则仍由 L1 手写产出。
 * mode 在 while/foreach 间切换（docs/45），标题随模式变化。
 */
export function LoopConfig({ config, update, variablePaths, targetOptions, diagnostics }: Props) {
  const isForeach = config.mode === 'foreach'
  return (
    <>
      <Typography.Text strong>
        {isForeach
          ? '遍历循环（逐项执行循环体，04 §5.3 / docs/45）'
          : '条件循环（表达式为真时进入循环体，04 §5.3）'}
      </Typography.Text>
      <NodeConfigForm
        kind="loop"
        config={config}
        update={update}
        variablePaths={variablePaths}
        targetOptions={targetOptions}
        diagnostics={diagnostics}
      />
    </>
  )
}
