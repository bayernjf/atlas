import { Typography } from 'antd'
import { NodeConfigForm } from '../../lib/forms/NodeConfigForm'
import type { WidgetTargetOption } from '../../lib/forms/types'
import type { NodeConfig, NodeKind } from '../../lib/nodeCatalog'
import type { Diagnostic } from '../../lib/validation/diagnostics'
import { useTranslation } from '../../locales'

type Props = {
  kind: 'intent_recognition' | 'info_extraction' | 'content_generation'
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
  variablePaths: string[]
  targetOptions: WidgetTargetOption[]
  diagnostics: Diagnostic[]
}

/**
 * 三 LLM 结构化节点属性表单（docs/109 打包 AB，U1276）：
 * 走 forms 内核（数据 schema lib/schemas/nodes/* + UISchema nodeUiSchemas）——
 * intents/fields 数组、template/style/maxLength 文本与数值控件由 FormRenderer 渲染。
 */
export function StructuredConfig({
  kind,
  config,
  update,
  variablePaths,
  targetOptions,
  diagnostics,
}: Props) {
  const { t } = useTranslation('editor')
  const titleKey: Record<typeof kind, string> = {
    intent_recognition: 'nodeTitles.intent_recognition',
    info_extraction: 'nodeTitles.info_extraction',
    content_generation: 'nodeTitles.content_generation',
  }
  return (
    <>
      <Typography.Text strong>{t(titleKey[kind])}</Typography.Text>
      <NodeConfigForm
        kind={kind as NodeKind}
        config={config}
        update={update}
        variablePaths={variablePaths}
        targetOptions={targetOptions}
        diagnostics={diagnostics}
      />
    </>
  )
}
