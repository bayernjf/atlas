import { useEffect, useState } from 'react'
import { Button, Empty, Input, Select, Space, Typography } from 'antd'
import { listGraphs, type SavedGraphSummary } from '../../lib/apiClient'
import type { NodeConfig } from '../../lib/nodeCatalog'

type Props = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
  variablePaths: string[]
}

export function SubgraphConfig({ config, update, variablePaths }: Props) {
  const [graphs, setGraphs] = useState<SavedGraphSummary[]>([])
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    listGraphs()
      .then(setGraphs)
      .catch((error: Error) => setLoadError(error.message))
  }, [])

  const inputs = config.inputs ?? {}
  const entries = Object.entries(inputs)

  const patchInputs = (next: Record<string, string>) => update({ inputs: next })

  const renameKey = (oldKey: string, newKey: string) => {
    const next: Record<string, string> = {}
    for (const [key, value] of Object.entries(inputs)) {
      next[key === oldKey ? newKey : key] = value
    }
    patchInputs(next)
  }

  const setValue = (key: string, value: string) => patchInputs({ ...inputs, [key]: value })
  const removeRow = (key: string) => {
    const next = { ...inputs }
    delete next[key]
    patchInputs(next)
  }
  const addRow = () => {
    let index = entries.length
    let key = `input${index + 1}`
    while (key in inputs) {
      index += 1
      key = `input${index + 1}`
    }
    patchInputs({ ...inputs, [key]: '' })
  }

  return (
    <>
      <Typography.Text strong>子图设置（引用已保存图，沿唯一出边继续，04 §5.7）</Typography.Text>
      <label className="property-field">
        <Typography.Text type="secondary">引用子图</Typography.Text>
        {graphs.length === 0 && !loadError ? (
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="还没有已保存的图：先在编辑器搭好子流程并运行一次保存"
          />
        ) : (
          <Select
            style={{ width: '100%' }}
            placeholder="选择已保存的图"
            value={config.graphId || undefined}
            status={!config.graphId?.trim() ? 'error' : undefined}
            onChange={(graphId) => update({ graphId })}
            options={graphs.map((item) => ({
              value: item.id,
              label: `${item.id}（${item.node_count} 节点）`,
            }))}
          />
        )}
        {loadError && <Typography.Text type="danger">{loadError}</Typography.Text>}
      </label>
      <label className="property-field">
        <Typography.Text type="secondary">子图入参映射（键 = 子图入参，值支持父图 {'{{路径}}'}）</Typography.Text>
        <Space orientation="vertical" style={{ width: '100%' }} size="small">
          {entries.map(([key, value]) => (
            <div key={key} className="subgraph-input-row">
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  style={{ width: '40%' }}
                  placeholder="入参键"
                  value={key}
                  status={!key.trim() ? 'error' : undefined}
                  onChange={(event) => renameKey(key, event.target.value)}
                />
                <Input.TextArea
                  style={{ width: '50%' }}
                  autoSize={{ minRows: 1, maxRows: 4 }}
                  placeholder='{{trigger-1.context.payload.order_id}}'
                  value={value}
                  status={!value.trim() ? 'error' : undefined}
                  onChange={(event) => setValue(key, event.target.value)}
                />
                <Button danger onClick={() => removeRow(key)}>
                  删
                </Button>
              </Space.Compact>
              <Select
                style={{ width: '100%', marginTop: 4 }}
                placeholder="选择后追加 {{变量}} 到映射值"
                value={undefined}
                onChange={(path: string) => setValue(key, `${value}{{${path}}}`)}
                options={variablePaths.map((path) => ({ value: path, label: `{{${path}}}` }))}
              />
            </div>
          ))}
          <Button size="small" onClick={addRow}>
            添加入参
          </Button>
        </Space>
      </label>
    </>
  )
}
