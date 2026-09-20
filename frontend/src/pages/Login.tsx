import { useState } from 'react'
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd'
import { login } from '../lib/apiClient'
import type { Principal } from '../lib/auth'
import { useTranslation } from '../locales'

type LoginProps = {
  onLoggedIn: (principal: Principal) => void
}

type SeedRole = 'admin' | 'operator' | 'viewer'

const SEED_HINTS: Array<{ tenantKey: string; accounts: Array<{ user: string; pass: string; role: SeedRole }> }> = [
  {
    tenantKey: 'auth.login.seed.tenantA',
    accounts: [
      { user: 'admin-a', pass: 'admin123', role: 'admin' },
      { user: 'operator-a', pass: 'operator123', role: 'operator' },
      { user: 'viewer-a', pass: 'viewer123', role: 'viewer' },
    ],
  },
  {
    tenantKey: 'auth.login.seed.tenantB',
    accounts: [{ user: 'admin-b', pass: 'admin123', role: 'admin' }],
  },
]

export function Login({ onLoggedIn }: LoginProps) {
  const { t } = useTranslation()
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleFinish(values: { username: string; password: string }) {
    setSubmitting(true)
    setError(null)
    try {
      const session = await login(values.username.trim(), values.password)
      onLoggedIn(session.principal)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : t('auth.login.failed'))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        background: 'var(--atlas-color-bg-page)',
        padding: 16,
      }}
    >
      <Card style={{ width: 420 }}>
        <Typography.Title level={3} style={{ marginTop: 0 }}>
          {t('auth.login.title')}
        </Typography.Title>
        <Typography.Paragraph type="secondary">{t('auth.login.subtitle')}</Typography.Paragraph>
        {error && (
          <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />
        )}
        <Form layout="vertical" onFinish={handleFinish} requiredMark={false}>
          <Form.Item
            label={t('auth.login.username')}
            name="username"
            rules={[{ required: true, message: t('auth.login.usernameRequired') }]}
          >
            <Input placeholder="admin-a" autoComplete="username" />
          </Form.Item>
          <Form.Item
            label={t('auth.login.password')}
            name="password"
            rules={[{ required: true, message: t('auth.login.passwordRequired') }]}
          >
            <Input.Password placeholder="admin123" autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            {t('auth.login.submit')}
          </Button>
        </Form>
        <Space orientation="vertical" size={4} style={{ marginTop: 20, width: '100%' }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t('auth.login.seedHintTitle')}
          </Typography.Text>
          {SEED_HINTS.map((group) => (
            <div key={group.tenantKey}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }} strong>
                {t(group.tenantKey)}
              </Typography.Text>
              {group.accounts.map((account) => (
                <Typography.Paragraph
                  key={account.user}
                  type="secondary"
                  style={{ fontSize: 12, marginBottom: 0, paddingLeft: 8 }}
                >
                  {t('auth.login.seed.accountLine', {
                    user: account.user,
                    pass: account.pass,
                    role: t(`role.${account.role}`),
                  })}
                </Typography.Paragraph>
              ))}
            </div>
          ))}
        </Space>
      </Card>
    </div>
  )
}
