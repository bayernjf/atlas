import { Button, Input, Radio, Select, Space, Typography } from 'antd'
import {
  MAX_PARALLEL_BRANCHES,
  MIN_PARALLEL_BRANCHES,
  type NodeConfig,
  type ParallelBranch,
} from '../../lib/nodeCatalog'

type Props = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
  targetOptions: { value: string; label: string }[]
}

const EMPTY_BRANCH: ParallelBranch = { label: '', target: '' }

export function ParallelConfig({ config, update, targetOptions }: Props) {
  const branches = (config.branches ?? []) as ParallelBranch[]

  const patchBranch = (index: number, patch: Partial<ParallelBranch>) => {
    update({
      branches: branches.map((branch, i) => (i === index ? { ...branch, ...patch } : branch)),
    })
  }

  return (
    <>
      <Typography.Text strong>并行扇出（同时执行各分支后汇聚，04 §5.4）</Typography.Text>
      <label className="property-field">
        <Typography.Text type="secondary">汇聚策略</Typography.Text>
        <Radio.Group
          value={config.joinStrategy}
          onChange={(event) => update({ joinStrategy: event.target.value })}
        >
          <Space orientation="vertical" size={4}>
            <Radio value="all_success">全部成功：任一分支失败则汇聚状态为 failed（汇聚节点仍执行）</Radio>
            <Radio value="all_completed">全部完成：只要各分支都走到汇聚即视为成功</Radio>
          </Space>
        </Radio.Group>
      </label>
      {branches.map((branch, index) => (
        <Space
          key={index}
          orientation="vertical"
          size={4}
          style={{ width: '100%', padding: 8, border: '1px solid var(--atlas-color-border)' }}
        >
          <Input
            addonBefore={`分支 ${index + 1}`}
            placeholder="分支名，如：通知商家"
            value={branch.label}
            status={!branch.label.trim() ? 'error' : undefined}
            onChange={(event) => patchBranch(index, { label: event.target.value })}
          />
          <Space style={{ width: '100%' }}>
            <Select
              style={{ flex: 1 }}
              placeholder="分支入口节点（需先在画布连线）"
              value={branch.target || undefined}
              status={!branch.target ? 'error' : undefined}
              onChange={(target: string) => patchBranch(index, { target })}
              options={targetOptions}
            />
            <Button
              size="small"
              danger
              disabled={branches.length <= MIN_PARALLEL_BRANCHES}
              onClick={() => update({ branches: branches.filter((_, i) => i !== index) })}
            >
              删除
            </Button>
          </Space>
        </Space>
      ))}
      <Button
        size="small"
        disabled={branches.length >= MAX_PARALLEL_BRANCHES}
        onClick={() => update({ branches: [...branches, { ...EMPTY_BRANCH }] })}
      >
        添加分支（{branches.length}/{MAX_PARALLEL_BRANCHES}）
      </Button>
      <label className="property-field">
        <Typography.Text type="secondary">
          汇聚目标（各分支末端都连线到该节点；分支不得直连结束）
        </Typography.Text>
        <Select
          style={{ width: '100%' }}
          placeholder="选择汇聚目标节点"
          value={config.joinTarget || undefined}
          status={!config.joinTarget ? 'error' : undefined}
          onChange={(joinTarget: string) => update({ joinTarget })}
          options={targetOptions}
        />
      </label>
    </>
  )
}
