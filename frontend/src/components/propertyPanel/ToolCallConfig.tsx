import { useMemo } from 'react'
import { AutoComplete, Input, Select, Typography } from 'antd'
import { buildToolOptions } from '../../lib/adapters'
import { useAdapters } from '../../lib/useScope'
import type { NodeConfig } from '../../lib/nodeCatalog'

type Props = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
  variablePaths: string[]
  onInsert: (path: string) => void
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="property-field">
      <Typography.Text type="secondary">{label}</Typography.Text>
      {children}
    </label>
  )
}

export function ToolCallConfig({ config, update, variablePaths, onInsert }: Props) {
  const { adapters, fetchFailed } = useAdapters()

  const groups = useMemo(() => (adapters ? buildToolOptions(adapters) : []), [adapters])
  const knownValues = useMemo(
    () => new Set(groups.flatMap((group) => group.options.map((option) => option.value))),
    [groups],
  )

  const toolMissing = !config.tool?.trim()

  const paramsPlaceholder = useMemo(() => {
    const tool = config.tool
    if (tool?.startsWith('http/')) {
      return '{"method":"GET","url":"/orders","headers":{"X-Demo-Token":"demo-token"}}'
    }
    if (tool === 'database/query') {
      return '{"sql":"SELECT order_id, amount FROM orders WHERE amount > :min","params":{"min":1000},"limit":500}'
    }
    if (tool === 'database/execute') {
      return '{"sql":"UPDATE orders SET status = :status WHERE order_id = :id","params":{"status":"refunded","id":"12345"}}'
    }
    if (tool === 'message/send') {
      return '{"channel":"email","to":["ops@example.com"],"subject":"订单 {{trigger-1.context.payload.order_id}} 待审批","body":"请处理"}'
    }
    return '{"element_desc": "提交按钮"}'
  }, [config.tool])
  // 已保存图里的工具（如未注册适配器）不在发现列表时补一条，保证 Select 能显示当前值
  const selectGroups = useMemo(() => {
    if (config.tool && !knownValues.has(config.tool)) {
      return [{ label: '当前值（未在已注册列表）', options: [{ value: config.tool, label: config.tool }] }, ...groups]
    }
    return groups
  }, [config.tool, knownValues, groups])

  return (
    <>
      <Field label="工具（适配器/能力）">
        {fetchFailed ? (
          <AutoComplete
            style={{ width: '100%' }}
            value={config.tool}
            status={toolMissing ? 'error' : undefined}
            onChange={(tool) => update({ tool })}
            options={selectGroups.flatMap((group) => group.options)}
            placeholder="适配器发现失败，可手动输入，如 http/request"
          />
        ) : (
          <Select
            showSearch
            style={{ width: '100%' }}
            value={config.tool || undefined}
            status={toolMissing ? 'error' : undefined}
            placeholder={adapters ? '选择已注册工具' : '加载可用工具…'}
            loading={adapters === null}
            onChange={(tool) => update({ tool })}
            options={selectGroups}
            optionFilterProp="label"
          />
        )}
      </Field>
      <Field label="参数映射（支持 {{路径}} 引用）">
        <Input.TextArea
          rows={4}
          placeholder={paramsPlaceholder}
          value={config.params}
          onChange={(event) => update({ params: event.target.value })}
        />
      </Field>
      <Field label="插入变量引用">
        <Select
          style={{ width: '100%' }}
          value={undefined}
          placeholder="选择后追加到参数"
          onChange={onInsert}
          options={variablePaths.map((path) => ({ value: path, label: `{{${path}}}` }))}
        />
      </Field>
    </>
  )
}
