import { Button, Input, InputNumber, Radio, Space, Typography } from 'antd'
import {
  MAX_ABSOLUTE_TIME_LENGTH,
  MAX_DURATION_EXPRESSION_LENGTH,
  MAX_EVENT_KEY_LENGTH,
  MAX_EVENT_KEYS,
  MAX_EVENT_WAIT_SECONDS,
  MAX_JITTER_SECONDS,
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

  const jitter = config.jitterSeconds
  const jitterInvalid =
    jitter !== undefined &&
    (!Number.isInteger(jitter) || jitter < 0 || jitter > MAX_JITTER_SECONDS)

  const durationMode =
    config.durationMode === 'dynamic'
      ? 'dynamic'
      : config.durationMode === 'absolute'
        ? 'absolute'
        : 'static'
  const durationExpression = config.durationExpression ?? ''
  const durationExpressionInvalid =
    !durationExpression.trim() ||
    durationExpression.length > MAX_DURATION_EXPRESSION_LENGTH

  const absoluteTime = config.absoluteTime ?? ''
  const absoluteTimeInvalid =
    !absoluteTime.trim() || absoluteTime.trim().length > MAX_ABSOLUTE_TIME_LENGTH

  const eventKey = config.eventKey ?? ''
  const eventKeyInvalid =
    !eventKey.trim() ||
    eventKey.length > MAX_EVENT_KEY_LENGTH ||
    !eventKeyStaticValid(eventKey)

  // docs/54：eventKeys 为数组即多事件竞速模式，与单键 eventKey 互斥。
  const multiEvent = Array.isArray(config.eventKeys)
  const eventKeys = Array.isArray(config.eventKeys) ? config.eventKeys : []
  const setEventKeyAt = (idx: number, value: string) => {
    const next = [...eventKeys]
    next[idx] = value
    update({ eventKeys: next })
  }
  const addEventKey = () => {
    if (eventKeys.length < MAX_EVENT_KEYS) update({ eventKeys: [...eventKeys, ''] })
  }
  const removeEventKey = (idx: number) => {
    update({ eventKeys: eventKeys.filter((_, i) => i !== idx) })
  }
  const switchEventMode = (multi: boolean) => {
    if (multi) {
      update({ eventKey: undefined, eventKeys: eventKeys.length ? eventKeys : [''] })
    } else {
      // docs/55：单键无 AND 语义，切回单事件时清掉 eventWaitMode（默认 any）。
      update({ eventKeys: undefined, eventKey: eventKey || '', eventWaitMode: undefined })
    }
  }
  // docs/55：多事件命中方式，any=OR 首决（默认），all=AND 全命中（需 ≥2 键，L1 兜底）。
  const eventWaitMode = config.eventWaitMode === 'all' ? 'all' : 'any'
  const allModeTooFewKeys = eventWaitMode === 'all' && eventKeys.length < 2

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
        <>
          <label className="property-field">
            <Typography.Text type="secondary">时长模式</Typography.Text>
            <Radio.Group
              value={durationMode}
              onChange={(event) => update({ durationMode: event.target.value })}
            >
              <Radio value="static">固定时长</Radio>
              <Radio value="dynamic">动态表达式</Radio>
              <Radio value="absolute">到点时刻</Radio>
            </Radio.Group>
          </label>
          {durationMode === 'static' ? (
            <>
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
              <label className="property-field">
                <Typography.Text type="secondary">抖动上限（可选）</Typography.Text>
                <Space.Compact>
                  <InputNumber
                    min={0}
                    max={MAX_JITTER_SECONDS}
                    step={1}
                    precision={0}
                    value={jitter}
                    status={jitterInvalid ? 'error' : undefined}
                    onChange={(value) => update({ jitterSeconds: value ?? undefined })}
                  />
                  <Button disabled>秒</Button>
                </Space.Compact>
                {jitterInvalid ? (
                  <Typography.Text type="danger">
                    抖动上限需为 0-{MAX_JITTER_SECONDS} 秒的整数
                  </Typography.Text>
                ) : (
                  <Typography.Text type="secondary">
                    实际等待 = 等待时长 + 0~抖动上限之间的随机秒（docs/54，防雪崩）
                  </Typography.Text>
                )}
              </label>
            </>
              ) : durationMode === 'absolute' ? (
            <label className="property-field">
              <Typography.Text type="secondary">到点时刻</Typography.Text>
              <Input
                value={absoluteTime}
                placeholder="2026-09-23T18:00:00+08:00 或 epoch 秒，支持 {{}}"
                status={absoluteTimeInvalid ? 'error' : undefined}
                onChange={(event) =>
                  update({ absoluteTime: event.target.value })
                }
              />
              {absoluteTimeInvalid ? (
                <Typography.Text type="danger">
                  必填，1-{MAX_ABSOLUTE_TIME_LENGTH} 字符
                </Typography.Text>
              ) : (
                <Typography.Text type="secondary">
                  运行时解析，须为未来 1-{MAX_WAIT_SECONDS} 秒内的时刻
                </Typography.Text>
              )}
            </label>
          ) : (
            <label className="property-field">
              <Typography.Text type="secondary">时长表达式</Typography.Text>
              <Input
                value={durationExpression}
                placeholder="{{global.slaHours}} * 3600"
                status={durationExpressionInvalid ? 'error' : undefined}
                onChange={(event) =>
                  update({ durationExpression: event.target.value })
                }
              />
              {durationExpressionInvalid ? (
                <Typography.Text type="danger">
                  必填，1-{MAX_DURATION_EXPRESSION_LENGTH} 字符
                </Typography.Text>
              ) : (
                <Typography.Text type="secondary">
                  运行时求值，须为 {MIN_WAIT_SECONDS}-{MAX_WAIT_SECONDS} 秒
                </Typography.Text>
              )}
            </label>
          )}
        </>
      ) : (
        <>
          <label className="property-field">
            <Typography.Text type="secondary">事件数量</Typography.Text>
            <Radio.Group
              value={multiEvent ? 'multi' : 'single'}
              onChange={(event) => switchEventMode(event.target.value === 'multi')}
            >
              <Radio value="single">单事件</Radio>
              <Radio value="multi">多事件竞速（任一命中即继续）</Radio>
            </Radio.Group>
          </label>
          {multiEvent ? (
            <label className="property-field">
              <Typography.Text type="secondary">
                事件标识（{eventKeys.length}/{MAX_EVENT_KEYS}，OR 竞速，首达者胜出）
              </Typography.Text>
              {eventKeys.map((key, idx) => (
                <Space key={idx} style={{ display: 'flex', marginBottom: 4 }}>
                  <Input
                    value={key}
                    placeholder={`order_event_${idx + 1}_{{trigger-1.context.payload.id}}`}
                    status={
                      key.trim() &&
                      (key.length > MAX_EVENT_KEY_LENGTH || !eventKeyStaticValid(key))
                        ? 'error'
                        : undefined
                    }
                    onChange={(event) => setEventKeyAt(idx, event.target.value)}
                  />
                  <Button
                    disabled={eventKeys.length <= 1}
                    onClick={() => removeEventKey(idx)}
                  >
                    删除
                  </Button>
                </Space>
              ))}
              <Button
                type="dashed"
                size="small"
                disabled={eventKeys.length >= MAX_EVENT_KEYS}
                onClick={addEventKey}
              >
                ＋添加事件（最多 {MAX_EVENT_KEYS} 个）
              </Button>
              <Typography.Text type="secondary">
                每个标识 1-{MAX_EVENT_KEY_LENGTH} 字符，静态部分仅允许字母、数字及 :_-
              </Typography.Text>
            </label>
          ) : null}
          {multiEvent && (
            <label className="property-field">
              <Typography.Text type="secondary">命中方式（docs/55）</Typography.Text>
              <Radio.Group
                value={eventWaitMode}
                onChange={(event) =>
                  update({
                    eventWaitMode: event.target.value === 'all' ? 'all' : undefined,
                  })
                }
              >
                <Radio value="any">任一命中即继续（OR，首达者胜出）</Radio>
                <Radio value="all" disabled={eventKeys.length < 2}>
                  全部命中才继续（AND，需至少 2 个事件）
                </Radio>
              </Radio.Group>
              {allModeTooFewKeys && (
                <Typography.Text type="danger">
                  全部命中（AND）需配置至少 2 个事件
                </Typography.Text>
              )}
            </label>
          )}
          {multiEvent ? null : (
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
          )}
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
