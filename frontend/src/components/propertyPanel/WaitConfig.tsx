import { Button, InputNumber, Radio, Space, Tooltip, Typography } from 'antd'
import { MAX_WAIT_SECONDS, MIN_WAIT_SECONDS, type NodeConfig } from '../../lib/nodeCatalog'

type Props = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
}

export function WaitConfig({ config, update }: Props) {
  const seconds = config.durationSeconds
  const outOfRange =
    seconds === undefined ||
    !Number.isInteger(seconds) ||
    seconds < MIN_WAIT_SECONDS ||
    seconds > MAX_WAIT_SECONDS

  return (
    <>
      <Typography.Text strong>等待设置（挂起后沿唯一出边继续，04 §5.5）</Typography.Text>
      <label className="property-field">
        <Typography.Text type="secondary">等待类型</Typography.Text>
        <Radio.Group value={config.waitType === 'duration' ? 'duration' : 'event'}>
          <Radio value="duration">定时等待</Radio>
          <Tooltip title="事件等待随持久化层开放（缓做 D19）">
            <Radio value="event" disabled>
              事件等待
            </Radio>
          </Tooltip>
        </Radio.Group>
      </label>
      <label className="property-field">
        <Typography.Text type="secondary">等待时长</Typography.Text>
        <Space.Compact>
          <InputNumber
            min={MIN_WAIT_SECONDS}
            max={MAX_WAIT_SECONDS}
            step={1}
            precision={0}
            value={seconds}
            status={outOfRange ? 'error' : undefined}
            onChange={(value) => update({ durationSeconds: value ?? undefined })}
          />
          <Button disabled>秒</Button>
        </Space.Compact>
        {outOfRange && (
          <Typography.Text type="danger">
            等待时长需为 {MIN_WAIT_SECONDS}-{MAX_WAIT_SECONDS} 秒的整数
          </Typography.Text>
        )}
      </label>
    </>
  )
}
