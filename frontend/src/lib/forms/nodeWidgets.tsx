/**
 * 节点表单业务控件（M4，04 §4.10 扩展 / 03 `form_renderer`）。
 *
 * 节点 config 里「连线目标选择」（x-ref 字段，如 human_approval 的双目标、
 * loop 的循环体/退出目标）需要当前图的节点候选，不属于通用内置控件。本文件
 * 只放组件；控件名常量在 types.ts、注册表在 nodeRegistry.ts（保 fast-refresh
 * 单一导出：组件文件不混出函数/常量）。
 */
import { Select } from 'antd'
import type { WidgetComponent } from './types'
import { DiagnosticText } from './widgets'

/**
 * 连线目标选择：值为目标节点 id；必填/悬空引用的错误由 L1/L2 诊断经
 * DiagnosticText 承接（与旧手写面板的 status:'error' 等价）。不允许清空
 * （对齐旧面板：目标字段必填，清空等同未选，应回到占位+报错态）。
 */
export const TargetSelectWidget: WidgetComponent = ({
  value,
  onChange,
  scope,
  diagnostics,
  placeholder,
}) => {
  const options = scope?.listNodeTargets?.() ?? []
  return (
    <>
      <Select
        showSearch
        allowClear={false}
        style={{ width: '100%' }}
        value={value ? String(value) : undefined}
        placeholder={placeholder ?? '选择目标节点'}
        status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
        onChange={(next: string) => onChange(next)}
        options={options}
        optionFilterProp="label"
      />
      <DiagnosticText diagnostics={diagnostics} />
    </>
  )
}
