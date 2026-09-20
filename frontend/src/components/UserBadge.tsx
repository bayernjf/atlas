import { Button, Space, Tag, Typography } from 'antd'
import { type Principal } from '../lib/auth'
import { useTranslation } from '../locales'

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
  const { t } = useTranslation()
  return (
    <Space size="middle">
      <Typography.Text strong style={{ color: 'rgba(255,255,255,0.85)' }}>
        {principal.tenant_name} · {principal.display_name}
      </Typography.Text>
      <Tag color={ROLE_COLORS[principal.role]}>{t(`role.${principal.role}`)}</Tag>
      <Button size="small" ghost onClick={onLogout}>
        {t('auth.session.logout')}
      </Button>
    </Space>
  )
}
