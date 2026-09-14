import { Button, Input, Select, Space, Typography } from 'antd'
import {
  type ConditionBranch,
  type NodeConfig,
} from '../../lib/nodeCatalog'
import { validateExpression } from '../../lib/conditions'

type Props = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
  variablePaths: string[]
  targetOptions: { value: string; label: string }[]
}

const EMPTY_BRANCH: ConditionBranch = { label: '', expression: '', target: '' }

export function ConditionConfig({ config, update, variablePaths, targetOptions }: Props) {
  const branches = (config.branches ?? []) as ConditionBranch[]

  const patchBranch = (index: number, patch: Partial<ConditionBranch>) => {
    update({
      branches: branches.map((branch, i) => (i === index ? { ...branch, ...patch } : branch)),
    })
  }

  return (
    <>
      <Typography.Text strong>条件分支（自上而下短路求值，04 §5.2）</Typography.Text>
      {branches.map((branch, index) => {
        const expressionErrors = branch.expression.trim()
          ? validateExpression(branch.expression)
          : []
        return (
          <Space
            key={index}
            orientation="vertical"
            size={4}
            style={{ width: '100%', padding: 8, border: '1px solid var(--atlas-color-border)' }}
          >
            <Input
              addonBefore="分支名"
              placeholder="如：大额"
              value={branch.label}
              status={!branch.label.trim() ? 'error' : undefined}
              onChange={(event) => patchBranch(index, { label: event.target.value })}
            />
            <Input.TextArea
              rows={2}
              placeholder="{{trigger-1.context.payload.amount}} > 1000"
              value={branch.expression}
              status={expressionErrors.length > 0 ? 'error' : undefined}
              onChange={(event) => patchBranch(index, { expression: event.target.value })}
            />
            {expressionErrors.length > 0 && (
              <Typography.Text type="danger" style={{ fontSize: 12 }}>
                {expressionErrors.join('；')}
              </Typography.Text>
            )}
            <Select
              style={{ width: '100%' }}
              placeholder="选择后追加 {{变量}} 到表达式"
              value={undefined}
              onChange={(path: string) =>
                patchBranch(index, { expression: `${branch.expression}{{${path}}} ` })
              }
              options={variablePaths.map((path) => ({ value: path, label: `{{${path}}}` }))}
            />
            <Space style={{ width: '100%' }} >
              <Select
                style={{ flex: 1 }}
                placeholder="目标节点（需先在画布连线）"
                value={branch.target || undefined}
                status={!branch.target ? 'error' : undefined}
                onChange={(target: string) => patchBranch(index, { target })}
                options={targetOptions}
              />
              <Button
                size="small"
                danger
                onClick={() => update({ branches: branches.filter((_, i) => i !== index) })}
              >
                删除
              </Button>
            </Space>
          </Space>
        )
      })}
      <Button size="small" onClick={() => update({ branches: [...branches, { ...EMPTY_BRANCH }] })}>
        添加分支
      </Button>
      <label className="property-field">
        <Typography.Text type="secondary">默认分支（所有条件均不满足时，必填）</Typography.Text>
        <Select
          style={{ width: '100%' }}
          placeholder="选择默认目标节点"
          value={config.defaultTarget || undefined}
          status={!config.defaultTarget ? 'error' : undefined}
          onChange={(defaultTarget: string) => update({ defaultTarget })}
          options={targetOptions}
        />
      </label>
    </>
  )
}
