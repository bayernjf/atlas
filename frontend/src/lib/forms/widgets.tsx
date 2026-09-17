/**
 * 八件内置表单控件（M3，04 §4.10）：全部由既有 AntD 组件承载，零新依赖。
 *
 * text/number/select/textarea/switch 为类型默认控件；json 是白名单外结构的
 * 统一降级（多行 JSON 文本，即旧 ToolCallConfig TextArea 路径）；expression
 * 为单行模板表达式；variable-input 为模板文本控件（M0 作用域变量补全 + M2
 * token 区间字段内高亮）。
 */
import { useState, type ReactElement } from 'react'
import { Input, InputNumber, Radio, Select, Switch, Typography } from 'antd'
import { splitTokenSegments } from './formTree'
import type { WidgetComponent, WidgetProps } from './types'

/** enum/const 选项的展示文案：UISchema optionLabels 优先，缺省回退原始值字符串。 */
function optionLabelOf(option: unknown, optionLabels?: Record<string, string>): string {
  const key = String(option)
  return optionLabels?.[key] ?? key
}

export function DiagnosticText({ diagnostics }: { diagnostics?: WidgetProps['diagnostics'] }): ReactElement | null {
  if (!diagnostics || diagnostics.length === 0) return null
  return (
    <div style={{ marginTop: 4 }}>
      {diagnostics.map((diagnostic, index) => (
        <Typography.Text
          key={`${diagnostic.code}-${index}`}
          type={diagnostic.severity === 'error' ? 'danger' : 'warning'}
          style={{ display: 'block', fontSize: 12 }}
        >
          {diagnostic.message}
        </Typography.Text>
      ))}
    </div>
  )
}

export const TextWidget: WidgetComponent = ({ value, onChange, schema, diagnostics, placeholder }) => (
  <>
    <Input
      value={value == null ? '' : String(value)}
      placeholder={placeholder ?? schema.description}
      onChange={(event) => onChange(event.target.value)}
      status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
    />
    <DiagnosticText diagnostics={diagnostics} />
  </>
)

export const NumberWidget: WidgetComponent = ({ value, onChange, schema, diagnostics, placeholder }) => (
  <>
    <InputNumber
      value={typeof value === 'number' ? value : null}
      min={schema.minimum}
      max={schema.maximum}
      placeholder={placeholder ?? schema.description}
      style={{ width: '100%' }}
      onChange={(next) => onChange(next ?? null)}
      status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
    />
    <DiagnosticText diagnostics={diagnostics} />
  </>
)

export const SelectWidget: WidgetComponent = ({ value, onChange, schema, optionLabels }) => {
  const options = schema.const !== undefined
    ? [schema.const]
    : (schema.enum ?? [])
  return (
    <Select
      value={value as never}
      options={options.map((option) => ({
        label: optionLabelOf(option, optionLabels),
        value: option as never,
      }))}
      disabled={schema.const !== undefined}
      style={{ width: '100%' }}
      onChange={(next) => onChange(next)}
      allowClear={schema.const === undefined}
    />
  )
}

/**
 * Radio 控件（M4 新增内置第九件）：enum/const 的单选按钮形态，供节点表单少量
 * 枚举（如 human_approval.onTimeout）保持与旧手写面板一致的并排单选；工具表单
 * enum 默认仍走 select，需在节点 schema 以 x-widget:'radio' 显式指定。
 */
export const RadioWidget: WidgetComponent = ({ value, onChange, schema, optionLabels }) => {
  const options = schema.const !== undefined ? [schema.const] : (schema.enum ?? [])
  return (
    <Radio.Group value={value} onChange={(event) => onChange(event.target.value)}>
      {options.map((option) => (
        <Radio key={String(option)} value={option as never}>
          {optionLabelOf(option, optionLabels)}
        </Radio>
      ))}
    </Radio.Group>
  )
}

export const TextareaWidget: WidgetComponent = ({ value, onChange, schema, diagnostics, placeholder, rows }) => (
  <>
    <Input.TextArea
      value={value == null ? '' : String(value)}
      rows={rows ?? 4}
      placeholder={placeholder ?? schema.description}
      onChange={(event) => onChange(event.target.value)}
      status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
    />
    <DiagnosticText diagnostics={diagnostics} />
  </>
)

export const SwitchWidget: WidgetComponent = ({ value, onChange }) => (
  <Switch checked={value === true} onChange={(checked) => onChange(checked)} />
)

export const ExpressionWidget: WidgetComponent = ({ value, onChange, schema, diagnostics, placeholder }) => (
  <>
    <Input
      value={value == null ? '' : String(value)}
      placeholder={placeholder ?? schema.description ?? '{{路径}} 表达式'}
      onChange={(event) => onChange(event.target.value)}
      style={{ fontFamily: 'var(--atlas-mono, monospace)' }}
      status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
    />
    <DiagnosticText diagnostics={diagnostics} />
  </>
)

/**
 * 模板文本控件：承载含 `{{路径}}` 的字符串值，并接入 M0 作用域补全与 M2 token 高亮。
 * - 变量补全：`scope.listPathsAt(当前节点)` 的可见路径清单，选中即追加 `{{路径}}`；
 * - 字段内高亮：`markers`（M2 renderMarkers 的 token 区间投影）把值里命中的
 *   `{{}}` 片段按诊断严重度染红/橙，未命中区间原样展示；无法映射到字段的诊断
 *   不在此高亮（精确聚合随 M4 Problems 面板）。
 */
export const VariableInputWidget: WidgetComponent = ({
  value,
  onChange,
  schema,
  diagnostics,
  markers,
  placeholder,
  rows,
  scope,
  nodeId,
}) => {
  const text = value == null ? '' : String(value)
  const variablePaths = scope?.listPathsAt(nodeId ?? '') ?? []
  const segments = markers && markers.length > 0 ? splitTokenSegments(text, markers, diagnostics) : []

  return (
    <>
      <Input.TextArea
        value={text}
        rows={rows ?? 3}
        placeholder={placeholder ?? schema.description ?? '可插入 {{节点输出.字段}} 变量'}
        onChange={(event) => onChange(event.target.value)}
        style={{ fontFamily: 'var(--atlas-mono, monospace)' }}
        status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
      />
      {variablePaths.length > 0 && (
        <Select
          size="small"
          style={{ width: '100%', marginTop: 4 }}
          value={undefined}
          placeholder="插入变量引用"
          onChange={(path: string) => onChange(appendToken(text, path))}
          options={variablePaths.map((path) => ({ value: path, label: `{{${path}}}` }))}
        />
      )}
      {segments.length > 0 && (
        <div
          style={{
            marginTop: 4,
            fontFamily: 'var(--atlas-mono, monospace)',
            fontSize: 12,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-all',
          }}
        >
          {segments.map((segment, index) =>
            segment.token ? (
              <Typography.Text
                key={index}
                type={segment.severity === 'error' ? 'danger' : 'warning'}
              >
                {segment.text}
              </Typography.Text>
            ) : (
              <Typography.Text key={index} type="secondary">
                {segment.text}
              </Typography.Text>
            ),
          )}
        </div>
      )}
      <DiagnosticText diagnostics={diagnostics} />
    </>
  )
}

/** 变量补全的追加语义：值尾追加 `{{路径}}`（与既有面板「插入变量引用」一致）。 */
function appendToken(text: string, path: string): string {
  return `${text}{{${path}}}`
}

/**
 * JSON 降级控件：多行 JSON 文本。允许暂时无法解析的草稿（不做部分解析）；
 * 解析成功时向上抛解析后的值，失败时抛原始字符串（L1 按类型错误报出）。
 */
export const JsonWidget: WidgetComponent = ({ value, onChange, schema, diagnostics, placeholder, rows }) => {
  // 聚焦期持本地草稿（允许暂时无法解析的文本）；失焦后回到由 value 派生的规范文本。
  const [draft, setDraft] = useState<string | null>(null)
  const text = draft ?? stringifyValue(value)

  return (
    <>
      <Input.TextArea
        value={text}
        rows={rows ?? 4}
        placeholder={placeholder ?? schema.description ?? '{ "key": "value" }'}
        onFocus={() => setDraft(stringifyValue(value))}
        onBlur={() => setDraft(null)}
        onChange={(event) => {
          setDraft(event.target.value)
          onChange(parseJsonOrRaw(event.target.value))
        }}
        style={{ fontFamily: 'var(--atlas-mono, monospace)' }}
        status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
      />
      <DiagnosticText diagnostics={diagnostics} />
    </>
  )
}

function stringifyValue(value: unknown): string {
  if (typeof value === 'string') return value
  if (value === undefined) return ''
  return JSON.stringify(value, null, 2)
}

function parseJsonOrRaw(text: string): unknown {
  if (text.trim() === '') return ''
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}


