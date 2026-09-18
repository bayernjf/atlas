/**
 * 节点表单业务控件（M4，04 §4.10 扩展 / 03 `form_renderer`）。
 *
 * 节点 config 里「连线目标选择」（x-ref 字段，如 human_approval 的双目标、
 * loop 的循环体/退出目标）需要当前图的节点候选，不属于通用内置控件。本文件
 * 只放组件；控件名常量在 types.ts、注册表在 nodeRegistry.ts（保 fast-refresh
 * 单一导出：组件文件不混出函数/常量）。
 */
import { useEffect, useState } from 'react'
import { Empty, Select, Typography } from 'antd'
import { listCards, listGraphs, type CardSummary, type SavedGraphSummary } from '../apiClient'
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

/**
 * 已保存子图选择（subgraph.graphId，M4 批 2 ⑨）：挂载时拉一次 /api/graphs。
 * 空态引导先搭子流程并保存；加载失败显错误但不阻塞编辑（与旧手写 SubgraphConfig 等价）。
 * 必填/悬空由 L1 诊断经 DiagnosticText 承接。
 */
export const SavedGraphSelectWidget: WidgetComponent = ({
  value,
  onChange,
  diagnostics,
  placeholder,
}) => {
  const [graphs, setGraphs] = useState<SavedGraphSummary[]>([])
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    let cancelled = false
    listGraphs()
      .then((items) => {
        if (!cancelled) setGraphs(items)
      })
      .catch((error: Error) => {
        if (!cancelled) setLoadError(error.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (graphs.length === 0 && !loadError) {
    return (
      <>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="还没有已保存的图：先在编辑器搭好子流程并运行一次保存"
        />
        <DiagnosticText diagnostics={diagnostics} />
      </>
    )
  }

  return (
    <>
      <Select
        showSearch
        allowClear={false}
        style={{ width: '100%' }}
        value={value ? String(value) : undefined}
        placeholder={placeholder ?? '选择已保存的图'}
        status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
        onChange={(next: string) => onChange(next)}
        optionFilterProp="label"
        options={graphs.map((item) => ({
          value: item.id,
          label: `${item.id}（${item.node_count} 节点）`,
        }))}
      />
      {loadError && <Typography.Text type="danger">{loadError}</Typography.Text>}
      <DiagnosticText diagnostics={diagnostics} />
    </>
  )
}

/**
 * 交互卡片选择（human_approval.cardTemplateId，M8）：挂载时拉一次 /api/cards。
 * 与子图选择不同，本字段可选：允许清空（allowClear），清空即回到 summary 旧路径；
 * 目录为空/加载失败也不阻塞编辑（该字段本就可留空）。
 */
export const CardSelectWidget: WidgetComponent = ({
  value,
  onChange,
  diagnostics,
  placeholder,
}) => {
  const [cards, setCards] = useState<CardSummary[]>([])
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    let cancelled = false
    listCards()
      .then((items) => {
        if (!cancelled) setCards(items)
      })
      .catch((error: Error) => {
        if (!cancelled) setLoadError(error.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (cards.length === 0 && !loadError) {
    return (
      <>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="暂无内置卡片：留空即使用默认审批说明"
        />
        <DiagnosticText diagnostics={diagnostics} />
      </>
    )
  }

  return (
    <>
      <Select
        showSearch
        allowClear
        style={{ width: '100%' }}
        value={value ? String(value) : undefined}
        placeholder={placeholder ?? '选择交互卡片（留空＝默认审批说明）'}
        status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
        onChange={(next: string | undefined) => onChange(next ?? '')}
        optionFilterProp="label"
        options={cards.map((item) => ({ value: item.id, label: `${item.name}（${item.id}）` }))}
      />
      {loadError && <Typography.Text type="danger">{loadError}</Typography.Text>}
      <DiagnosticText diagnostics={diagnostics} />
    </>
  )
}
