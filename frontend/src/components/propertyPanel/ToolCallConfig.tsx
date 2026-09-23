import { useMemo } from 'react'
import { AutoComplete, Input, Select, Typography } from 'antd'
import { buildToolOptions } from '../../lib/adapters'
import { FormRenderer } from '../../lib/forms/FormRenderer'
import { nlWarningDiagnostics } from '../../lib/forms/nlWarnings'
import { paramsToText, parseParamsObject } from '../../lib/forms/params'
import { buildToolSchemaTable, isFormRenderable } from '../../lib/forms/toolSchemas'
import { toolParamsPlaceholder } from '../../lib/forms/toolPlaceholder'
import { useAdapters } from '../../lib/useScope'
import { validateParamFields } from '../../lib/validation/l1'
import { useEditorStore } from '../../store/editorStore'
import type { NodeConfig } from '../../lib/nodeCatalog'
import { useTranslation } from '../../locales'

type Props = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
  variablePaths: string[]
  onInsert: (path: string) => void
  /** 当前节点 id：NL 参数警告按节点归属（04 §4.10）。 */
  nodeId: string
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="property-field">
      <Typography.Text type="secondary">{label}</Typography.Text>
      {children}
    </label>
  )
}

export function ToolCallConfig({ config, update, variablePaths, onInsert, nodeId }: Props) {
  const { t } = useTranslation('editor')
  const { adapters, fetchFailed } = useAdapters()
  const nlWarnings = useEditorStore((state) => state.nlWarnings)

  const groups = useMemo(() => (adapters ? buildToolOptions(adapters) : []), [adapters])
  const knownValues = useMemo(
    () => new Set(groups.flatMap((group) => group.options.map((option) => option.value))),
    [groups],
  )

  const toolMissing = !config.tool?.trim()

  // M3 第二来源：工具 input_schema 取自 /api/adapters 发现快照（04 §4.10）。
  const toolSchema = useMemo(() => {
    if (!config.tool) return null
    return buildToolSchemaTable(adapters)[config.tool] ?? null
  }, [adapters, config.tool])

  // params 仍是 JSON 字符串：可解析为对象 + 工具 schema 可表单化 → 表单路径；
  // 未注册工具、发现失败、空 schema、不可解析文本（含裸 {{}} 整串）→ 旧 JSON 文本框。
  const parsedParams = useMemo(() => parseParamsObject(config.params), [config.params])
  const formSource =
    parsedParams !== null && isFormRenderable(toolSchema)
      ? { schema: toolSchema, value: parsedParams }
      : null

  // 字段内变量补全：可见路径清单由 PropertyPanel 按当前节点预先算好（M0 ScopeIndex）。
  const widgetScope = useMemo(() => ({ listPathsAt: () => variablePaths }), [variablePaths])

  // NL 参数警告：wire 仍是 string[]，按节点归属挂到 params 根、非阻塞（04 §4.10）。
  const nlDiagnostics = useMemo(
    () => nlWarningDiagnostics(nlWarnings, nodeId),
    [nlWarnings, nodeId],
  )
  // 表单路径下的字段诊断：复用 M2 L1 对工具 schema 求值（pointer 相对 params 根）。
  const paramDiagnostics = useMemo(() => {
    if (parsedParams === null || !isFormRenderable(toolSchema)) return []
    return [...validateParamFields(toolSchema, parsedParams), ...nlDiagnostics]
  }, [parsedParams, toolSchema, nlDiagnostics])

  const paramsPlaceholder = useMemo(
    () => toolParamsPlaceholder(config.tool),
    [config.tool],
  )
  // 已保存图里的工具（如未注册适配器）不在发现列表时补一条，保证 Select 能显示当前值
  const selectGroups = useMemo(() => {
    if (config.tool && !knownValues.has(config.tool)) {
      return [{ label: t('tool.currentValue'), options: [{ value: config.tool, label: config.tool }] }, ...groups]
    }
    return groups
  }, [config.tool, knownValues, groups, t])

  return (
    <>
      <Field label={t('tool.field')}>
        {fetchFailed ? (
          <AutoComplete
            style={{ width: '100%' }}
            value={config.tool}
            status={toolMissing ? 'error' : undefined}
            onChange={(tool) => update({ tool })}
            options={selectGroups.flatMap((group) => group.options)}
            placeholder={t('tool.discoveryFailed')}
          />
        ) : (
          <Select
            showSearch
            style={{ width: '100%' }}
            value={config.tool || undefined}
            status={toolMissing ? 'error' : undefined}
            placeholder={adapters ? t('tool.pickRegistered') : t('tool.loading')}
            loading={adapters === null}
            onChange={(tool) => update({ tool })}
            options={selectGroups}
            optionFilterProp="label"
          />
        )}
      </Field>
      <Field label={t('tool.paramMapping')}>
        {formSource ? (
          <FormRenderer
            key={config.tool}
            schema={formSource.schema}
            value={formSource.value}
            onChange={(next) => update({ params: paramsToText(next) })}
            scope={widgetScope}
            nodeId={nodeId}
            diagnostics={paramDiagnostics}
          />
        ) : (
          <>
            <Input.TextArea
              rows={4}
              placeholder={paramsPlaceholder}
              value={config.params}
              onChange={(event) => update({ params: event.target.value })}
            />
            {nlDiagnostics.map((diagnostic, index) => (
              <Typography.Text
                key={`${diagnostic.code}-${index}`}
                type="warning"
                style={{ display: 'block', fontSize: 12 }}
              >
                {diagnostic.message}
              </Typography.Text>
            ))}
          </>
        )}
      </Field>
      {!formSource && (
        <Field label={t('tool.insertVar')}>
          <Select
            style={{ width: '100%' }}
            value={undefined}
            placeholder={t('tool.appendHint')}
            onChange={onInsert}
            options={variablePaths.map((path) => ({ value: path, label: `{{${path}}}` }))}
          />
        </Field>
      )}
    </>
  )
}
