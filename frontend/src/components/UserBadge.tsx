import { Button, Space, Tag, Typography } from 'antd'
import { ROLE_LABELS, type Principal } from '../lib/auth'

type UserBadgeProps = {
  principal: Principal
  onLogout: () => void
}

const ROLE_COLORS = {
  viewer: 'default',
  operator: 'blue',
  admin: 'gold',
} as const

export function UserBadge({ principal, onLogout }: UserBadgeProps) {
  return (
    <Space size="middle">
      <Typography.Text strong style={{ color: 'rgba(255,255,255,0.85)' }}>
        {principal.tenant_name} · {principal.display_name}
      </Typography.Text>
      <Tag color={ROLE_COLORS[principal.role]}>{ROLE_LABELS[principal.role]}</Tag>
      <Button size="small" ghost onClick={onLogout}>
        退出登录
      </Button>
    </Space>
  )
}
