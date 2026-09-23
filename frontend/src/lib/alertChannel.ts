import type { AlertChannelConfig, AlertChannelKind } from './apiClient'

export const ALERT_CHANNEL_OPTIONS: AlertChannelKind[] = [
  'dingtalk',
  'wecom',
  'feishu',
  'webhook',
  'email',
]

const URL_CHANNELS: AlertChannelKind[] = ['dingtalk', 'wecom', 'feishu', 'webhook']
const SECRET_CHANNELS: AlertChannelKind[] = ['dingtalk', 'feishu']

export type AlertChannelDraft = Pick<
  AlertChannelConfig,
  'enabled' | 'channel' | 'to' | 'secret' | 'minSeverity'
>

export function isUrlChannel(channel: AlertChannelKind): boolean {
  return URL_CHANNELS.includes(channel)
}

export function secretVisible(channel: AlertChannelKind): boolean {
  return SECRET_CHANNELS.includes(channel)
}

function isHttpUrl(value: string): boolean {
  const head = value.split('://')
  return head.length === 2 && ['http', 'https'].includes(head[0]) && head[1].length > 0
}

export function validateAlertChannelDraft(draft: AlertChannelDraft): string[] {
  const errors: string[] = []
  if (draft.secret.length > 200) errors.push('加签密钥长度不能超过 200')
  if (draft.secret && !secretVisible(draft.channel)) {
    errors.push('仅钉钉和飞书渠道支持加签密钥')
  }
  if (draft.enabled) {
    const target = draft.to.trim()
    if (!target) {
      errors.push('启用通知时必须填写通知目标')
    } else if (isUrlChannel(draft.channel)) {
      if (!isHttpUrl(target)) errors.push('通知目标必须是 http(s) 开头的完整 URL')
    } else if (!target.includes('@')) {
      errors.push('通知目标必须是有效的邮箱地址')
    }
  }
  return errors
}
