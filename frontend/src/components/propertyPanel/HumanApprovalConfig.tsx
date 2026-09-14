import { Button, Input, InputNumber, Radio, Select, Space, Typography } from 'antd'
import {
  MAX_APPROVAL_TIMEOUT,
  MIN_APPROVAL_TIMEOUT,
  type NodeConfig,
} from '../../lib/nodeCatalog'

type Props = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
  variablePaths: string[]
  targetOptions: { value: string; label: string }[]
}

export function HumanApprovalConfig({ config, update, variablePaths, targetOptions }: Props) {
  const summary = config.summary ?? ''
  const approver = config.approver ?? ''

  return (
    <>
      <Typography.Text strong>人机协作（暂停等待人工审批，04 §5.6）</Typography.Text>
      <label className="property-field">
        <Typography.Text type="secondary">审批说明（必填，支持 {'{{路径}}'} 引用）</Typography.Text>
        <Input.TextArea
          rows={3}
          placeholder="订单 {{trigger-1.context.payload.order_id}} 退款 ¥{{trigger-1.context.payload.amount}}，请人工复核"
          value={summary}
          status={!summary.trim() ? 'error' : undefined}
          onChange={(event) => update({ summary: event.target.value })}
        />
        <Select
          style={{ width: '100%', marginTop: 4 }}
          placeholder="选择后追加 {{变量}} 到审批说明"
          value={undefined}
          onChange={(path: string) => update({ summary: `${summary}{{${path}}} ` })}
          options={variablePaths.map((path) => ({ value: path, label: `{{${path}}}` }))}
        />
      </label>
      <label className="property-field">
        <Typography.Text type="secondary">审批人（可选，仅展示与审计，v1 不鉴权）</Typography.Text>
        <Input
          placeholder="客服主管"
          value={approver}
          onChange={(event) => update({ approver: event.target.value })}
        />
      </label>
      <label className="property-field">
        <Typography.Text type="secondary">
          超时时长（{MIN_APPROVAL_TIMEOUT}-{MAX_APPROVAL_TIMEOUT} 秒）
        </Typography.Text>
        <Space.Compact>
          <InputNumber
            min={MIN_APPROVAL_TIMEOUT}
            max={MAX_APPROVAL_TIMEOUT}
            step={10}
            precision={0}
            value={config.timeoutSeconds}
            onChange={(value) => update({ timeoutSeconds: value ?? undefined })}
          />
          <Button disabled>秒</Button>
        </Space.Compact>
      </label>
      <label className="property-field">
        <Typography.Text type="secondary">超时策略（默认自动拒绝；超时后 run 仍完成）</Typography.Text>
        <Radio.Group
          value={config.onTimeout ?? 'reject'}
          onChange={(event) => update({ onTimeout: event.target.value })}
        >
          <Radio value="reject">超时自动拒绝</Radio>
          <Radio value="approve">超时自动通过</Radio>
        </Radio.Group>
      </label>
      <Space orientation="vertical" size={8} style={{ width: '100%' }}>
        <label className="property-field" style={{ margin: 0 }}>
          <Typography.Text type="secondary">通过目标（人工同意 / 超时自动通过时进入）</Typography.Text>
          <Select
            style={{ width: '100%' }}
            placeholder="选择通过目标节点"
            value={config.approvedTarget || undefined}
            status={!config.approvedTarget ? 'error' : undefined}
            onChange={(approvedTarget: string) => update({ approvedTarget })}
            options={targetOptions}
          />
        </label>
        <label className="property-field" style={{ margin: 0 }}>
          <Typography.Text type="secondary">拒绝目标（人工拒绝 / 超时自动拒绝时进入）</Typography.Text>
          <Select
            style={{ width: '100%' }}
            placeholder="选择拒绝目标节点"
            value={config.rejectedTarget || undefined}
            status={!config.rejectedTarget ? 'error' : undefined}
            onChange={(rejectedTarget: string) => update({ rejectedTarget })}
            options={targetOptions}
          />
        </label>
      </Space>
    </>
  )
}
