import { describe, expect, it } from 'vitest'
import {
  secretVisible,
  validateAlertChannelDraft,
  type AlertChannelDraft,
} from '../alertChannel'

function draft(overrides: Partial<AlertChannelDraft> = {}): AlertChannelDraft {
  return {
    enabled: true,
    channel: 'dingtalk',
    to: 'https://robot.example.com/send',
    secret: '',
    minSeverity: 'critical',
    ...overrides,
  }
}

describe('secretVisible', () => {
  it('is true only for dingtalk and feishu', () => {
    expect(secretVisible('dingtalk')).toBe(true)
    expect(secretVisible('feishu')).toBe(true)
    expect(secretVisible('wecom')).toBe(false)
    expect(secretVisible('webhook')).toBe(false)
    expect(secretVisible('email')).toBe(false)
  })
})

describe('validateAlertChannelDraft', () => {
  it('accepts a valid url channel', () => {
    expect(validateAlertChannelDraft(draft())).toEqual([])
  })

  it('requires target when enabled', () => {
    expect(validateAlertChannelDraft(draft({ to: '  ' }))).toContain(
      '启用通知时必须填写通知目标',
    )
  })

  it('requires http(s) url for im/webhook channels', () => {
    expect(validateAlertChannelDraft(draft({ to: 'robot.example.com' }))).toContain(
      '通知目标必须是 http(s) 开头的完整 URL',
    )
  })

  it('requires @ for email target', () => {
    expect(validateAlertChannelDraft(
      draft({ channel: 'email', to: 'ops-example.com' }),
    )).toContain('通知目标必须是有效的邮箱地址')
  })

  it('accepts a valid email target', () => {
    expect(validateAlertChannelDraft(
      draft({ channel: 'email', to: 'ops@example.com' }),
    )).toEqual([])
  })

  it('rejects secret for non-secret channels', () => {
    expect(validateAlertChannelDraft(
      draft({ channel: 'wecom', secret: 'SEC' }),
    )).toContain('仅钉钉和飞书渠道支持加签密钥')
  })

  it('rejects secret longer than 200', () => {
    expect(validateAlertChannelDraft(draft({ secret: 'x'.repeat(201) }))).toContain(
      '加签密钥长度不能超过 200',
    )
  })

  it('skips content validation when disabled', () => {
    expect(validateAlertChannelDraft(
      draft({ enabled: false, to: '', secret: 'SEC' }),
    )).toEqual([])
  })
})
