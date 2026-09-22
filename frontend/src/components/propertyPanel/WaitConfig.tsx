import { Button, Input, InputNumber, Radio, Space, Typography } from 'antd'
import {
  MAX_EVENT_KEY_LENGTH,
  MAX_EVENT_WAIT_SECONDS,
  MAX_WAIT_SECONDS,
  MIN_EVENT_WAIT_SECONDS,
  MIN_WAIT_SECONDS,
  type NodeConfig,
} from '../../lib/nodeCatalog'
import { eventKeyStaticValid } from '../../lib/validation/l1'

type Props = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
}

export function WaitConfig({ config, update }: Props) {
  const waitType = config.waitType === 'event' ? 'event' : 'duration'

  const seconds = config.durationSeconds
  const durationInvalid =
    seconds === undefined ||
    !Number.isInteger(seconds) ||
    seconds < MIN_WAIT_SECONDS ||
    seconds > MAX_WAIT_SECONDS

  const eventKey = config.eventKey ?? ''
  const eventKeyInvalid =
    !eventKey.trim() ||
    eventKey.length > MAX_EVENT_KEY_LENGTH ||
    !eventKeyStaticValid(eventKey)

  const timeout = config.timeoutSeconds
  const timeoutInvalid =
    timeout === undefined ||
    !Number.isInteger(timeout) ||
    timeout < MIN_EVENT_WAIT_SECONDS ||
    timeout > MAX_EVENT_WAIT_SECONDS

  return (
    <>
      <Typography.Text strong>等待设置（挂起后沿唯一出边继续，04 §5.5）</Typography.Text>
      <label className="property-field">
        <Typography.Text type="secondary">等待类型</Typography.Text>
        <Radio.Group
          value={waitType}
          onChange={(event) => update({ waitType: event.target.value })}
        >
          <Radio value="duration">定时等待</Radio>
          <Radio value="event">事件等待</Radio>
        </Radio.Group>
      </label>
      {waitType === 'duration' ? (
        <label className="property-field">
          <Typography.Text type="secondary">等待时长</Typography.Text>
          <Space.Compact>
            <InputNumber
              min={MIN_WAIT_SECONDS}
              max={MAX_WAIT_SECONDS}
              step={1}
              precision={0}
              value={seconds}
              status={durationInvalid ? 'error' : undefined}
              onChange={(value) => update({ durationSeconds: value ?? undefined })}
            />
            <Button disabled>秒</Button>
          </Space.Compact>
          {durationInvalid && (
            <Typography.Text type="danger">
              等待时长需为 {MIN_WAIT_SECONDS}-{MAX_WAIT_SECONDS} 秒的整数
            </Typography.Text>
          )}
        </label>
      ) : (
        <>
          <label className="property-field">
            <Typography.Text type="secondary">事件标识</Typography.Text>
            <Input
              value={eventKey}
              placeholder="order_paid_{{trigger-1.context.payload.order_id}}"
              status={eventKeyInvalid ? 'error' : undefined}
              onChange={(event) => update({ eventKey: event.target.value })}
            />
            {eventKeyInvalid ? (
              <Typography.Text type="danger">
                必填，1-{MAX_EVENT_KEY_LENGTH} 字符；静态部分仅允许字母、数字及
                :_-，占位 {'{{路径}}'} 内不检查
              </Typography.Text>
            ) : (
              <Typography.Text type="secondary">
                支持 {'{{路径}}'} 插值；运行时渲染后再校验
              </Typography.Text>
            )}
          </label>
          <label className="property-field">
            <Typography.Text type="secondary">超时时间</Typography.Text>
            <Space.Compact>
              <InputNumber
                min={MIN_EVENT_WAIT_SECONDS}
                max={MAX_EVENT_WAIT_SECONDS}
                step={1}
                precision={0}
                value={timeout}
                status={timeoutInvalid ? 'error' : undefined}
                onChange={(value) => update({ timeoutSeconds: value ?? undefined })}
              />
              <Button disabled>秒</Button>
            </Space.Compact>
            {timeoutInvalid && (
              <Typography.Text type="danger">
                超时时间需为 {MIN_EVENT_WAIT_SECONDS}-{MAX_EVENT_WAIT_SECONDS} 秒的整数
              </Typography.Text>
            )}
          </label>
          <label className="property-field">
            <Typography.Text type="secondary">超时策略</Typography.Text>
            <Radio.Group
              value={config.onTimeout === 'fail' ? 'fail' : 'continue'}
              onChange={(event) => update({ onTimeout: event.target.value })}
            >
              <Radio value="continue">继续（超时沿出边继续）</Radio>
              <Radio value="fail">失败（超时使流程失败）</Radio>
            </Radio.Group>
          </label>
        </>
      )}
    </>
  )
}
