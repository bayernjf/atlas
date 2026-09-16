/**
 * 八件内置表单控件（M3，04 §4.10）：全部由既有 AntD 组件承载，零新依赖。
 *
 * text/number/select/textarea/switch 为类型默认控件；json 是白名单外结构的
 * 统一降级（多行 JSON 文本，即旧 ToolCallConfig TextArea 路径）；expression
 * 为单行模板表达式；variable-input 为多行模板文本（M3 第④步接入 scope 变量
 * 补全与 M2 token 诊断高亮，本步仅承载字符串值与字段级诊断文案）。
 */
import { useState, type ReactElement } from 'react'
import { Input, InputNumber, Select, Switch, Typography } from 'antd'
import type { WidgetComponent, WidgetProps } from './types'

function DiagnosticText({ diagnostics }: { diagnostics?: WidgetProps['diagnostics'] }): ReactElement | null {
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

export const SelectWidget: WidgetComponent = ({ value, onChange, schema }) => {
  const options = schema.const !== undefined
    ? [schema.const]
    : (schema.enum ?? [])
  return (
    <Select
      value={value as never}
      options={options.map((option) => ({ label: String(option), value: option as never }))}
      disabled={schema.const !== undefined}
      style={{ width: '100%' }}
      onChange={(next) => onChange(next)}
      allowClear={schema.const === undefined}
    />
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
 * 模板文本控件（第④步前的基础形态）：承载含 {{路径}} 的字符串值。
 * 变量补全与 token 区间红字高亮在第④步接入 scope/renderMarkers。
 */
export const VariableInputWidget: WidgetComponent = ({ value, onChange, schema, diagnostics, placeholder, rows }) => (
  <>
    <Input.TextArea
      value={value == null ? '' : String(value)}
      rows={rows ?? 3}
      placeholder={placeholder ?? schema.description ?? '可插入 {{节点输出.字段}} 变量'}
      onChange={(event) => onChange(event.target.value)}
      style={{ fontFamily: 'var(--atlas-mono, monospace)' }}
      status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
    />
    <DiagnosticText diagnostics={diagnostics} />
  </>
)

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


