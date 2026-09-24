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
import { useTranslation } from '../../locales'

type Props = {
  config: NodeConfig
  update: (patch: Partial<NodeConfig>) => void
}

export function WaitConfig({ config, update }: Props) {
  const { t } = useTranslation('editor')
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
      <Typography.Text strong>{t('wait.title')}</Typography.Text>
      <label className="property-field">
        <Typography.Text type="secondary">{t('wait.typeLabel')}</Typography.Text>
        <Radio.Group
          value={waitType}
          onChange={(event) => update({ waitType: event.target.value })}
        >
          <Radio value="duration">{t('wait.typeDuration')}</Radio>
          <Radio value="event">{t('wait.typeEvent')}</Radio>
        </Radio.Group>
      </label>
      {waitType === 'duration' ? (
        <>
          <label className="property-field">
            <Typography.Text type="secondary">{t('wait.durationModeLabel')}</Typography.Text>
            <Radio.Group
              value={durationMode}
              onChange={(event) => update({ durationMode: event.target.value })}
            >
              <Radio value="static">{t('wait.modeStatic')}</Radio>
              <Radio value="dynamic">{t('wait.modeDynamic')}</Radio>
              <Radio value="absolute">{t('wait.modeAbsolute')}</Radio>
            </Radio.Group>
          </label>
          {durationMode === 'static' ? (
            <>
              <label className="property-field">
                <Typography.Text type="secondary">{t('wait.durationLabel')}</Typography.Text>
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
                  <Button disabled>{t('wait.unitSecond')}</Button>
                </Space.Compact>
                {durationInvalid && (
                  <Typography.Text type="danger">
                    {t('wait.durationInvalid', { min: MIN_WAIT_SECONDS, max: MAX_WAIT_SECONDS })}
                  </Typography.Text>
                )}
              </label>
              <label className="property-field">
                <Typography.Text type="secondary">{t('wait.jitterLabel')}</Typography.Text>
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
                  <Button disabled>{t('wait.unitSecond')}</Button>
                </Space.Compact>
                {jitterInvalid ? (
                  <Typography.Text type="danger">
                    {t('wait.jitterInvalid', { max: MAX_JITTER_SECONDS })}
                  </Typography.Text>
                ) : (
                  <Typography.Text type="secondary">
                    {t('wait.jitterHint')}
                  </Typography.Text>
                )}
              </label>
            </>
              ) : durationMode === 'absolute' ? (
            <label className="property-field">
              <Typography.Text type="secondary">{t('wait.absoluteLabel')}</Typography.Text>
              <Input
                value={absoluteTime}
                placeholder={t('wait.absolutePlaceholder')}
                status={absoluteTimeInvalid ? 'error' : undefined}
                onChange={(event) =>
                  update({ absoluteTime: event.target.value })
                }
              />
              {absoluteTimeInvalid ? (
                <Typography.Text type="danger">
                  {t('wait.absoluteInvalid', { max: MAX_ABSOLUTE_TIME_LENGTH })}
                </Typography.Text>
              ) : (
                <Typography.Text type="secondary">
                  {t('wait.absoluteHint', { max: MAX_WAIT_SECONDS })}
                </Typography.Text>
              )}
            </label>
          ) : (
            <label className="property-field">
              <Typography.Text type="secondary">{t('wait.expressionLabel')}</Typography.Text>
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
                  {t('wait.expressionInvalid', { max: MAX_DURATION_EXPRESSION_LENGTH })}
                </Typography.Text>
              ) : (
                <Typography.Text type="secondary">
                  {t('wait.expressionHint', { min: MIN_WAIT_SECONDS, max: MAX_WAIT_SECONDS })}
                </Typography.Text>
              )}
            </label>
          )}
        </>
      ) : (
        <>
          <label className="property-field">
            <Typography.Text type="secondary">{t('wait.eventCountLabel')}</Typography.Text>
            <Radio.Group
              value={multiEvent ? 'multi' : 'single'}
              onChange={(event) => switchEventMode(event.target.value === 'multi')}
            >
              <Radio value="single">{t('wait.singleEvent')}</Radio>
              <Radio value="multi">{t('wait.multiEvent')}</Radio>
            </Radio.Group>
          </label>
          {multiEvent ? (
            <label className="property-field">
              <Typography.Text type="secondary">
                {t('wait.eventKeysLabel', { count: eventKeys.length, max: MAX_EVENT_KEYS })}
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
                    {t('common:button.delete')}
                  </Button>
                </Space>
              ))}
              <Button
                type="dashed"
                size="small"
                disabled={eventKeys.length >= MAX_EVENT_KEYS}
                onClick={addEventKey}
              >
                {t('wait.addEvent', { max: MAX_EVENT_KEYS })}
              </Button>
              <Typography.Text type="secondary">
                {t('wait.eventKeyRule', { max: MAX_EVENT_KEY_LENGTH })}
              </Typography.Text>
            </label>
          ) : null}
          {multiEvent && (
            <label className="property-field">
              <Typography.Text type="secondary">{t('wait.matchModeLabel')}</Typography.Text>
              <Radio.Group
                value={eventWaitMode}
                onChange={(event) =>
                  update({
                    eventWaitMode: event.target.value === 'all' ? 'all' : undefined,
                  })
                }
              >
                <Radio value="any">{t('wait.matchAny')}</Radio>
                <Radio value="all" disabled={eventKeys.length < 2}>
                  {t('wait.matchAll')}
                </Radio>
              </Radio.Group>
              {allModeTooFewKeys && (
                <Typography.Text type="danger">
                  {t('wait.matchAllTooFew')}
                </Typography.Text>
              )}
            </label>
          )}
          {multiEvent ? null : (
            <label className="property-field">
              <Typography.Text type="secondary">{t('wait.eventKeyLabel')}</Typography.Text>
              <Input
                value={eventKey}
                placeholder="order_paid_{{trigger-1.context.payload.order_id}}"
                status={eventKeyInvalid ? 'error' : undefined}
                onChange={(event) => update({ eventKey: event.target.value })}
              />
              {eventKeyInvalid ? (
                <Typography.Text type="danger">
                  {t('wait.eventKeyInvalid', { max: MAX_EVENT_KEY_LENGTH })}
                  {t('wait.eventKeyInvalidSuffix')}
                </Typography.Text>
              ) : (
                <Typography.Text type="secondary">
                  {t('wait.eventKeyHint')}
                </Typography.Text>
              )}
            </label>
          )}
          <label className="property-field">
            <Typography.Text type="secondary">{t('wait.timeoutLabel')}</Typography.Text>
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
              <Button disabled>{t('wait.unitSecond')}</Button>
            </Space.Compact>
            {timeoutInvalid && (
              <Typography.Text type="danger">
                {t('wait.timeoutInvalid', { min: MIN_EVENT_WAIT_SECONDS, max: MAX_EVENT_WAIT_SECONDS })}
              </Typography.Text>
            )}
          </label>
          <label className="property-field">
            <Typography.Text type="secondary">{t('wait.timeoutPolicyLabel')}</Typography.Text>
            <Radio.Group
              value={config.onTimeout === 'fail' ? 'fail' : 'continue'}
              onChange={(event) => update({ onTimeout: event.target.value })}
            >
              <Radio value="continue">{t('wait.onTimeoutContinue')}</Radio>
              <Radio value="fail">{t('wait.onTimeoutFail')}</Radio>
            </Radio.Group>
          </label>
        </>
      )}
    </>
  )
}
