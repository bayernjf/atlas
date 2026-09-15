import { useState } from 'react'
import { Alert, Button, Card, Form, Input, Space, Typography } from 'antd'
import { login } from '../lib/apiClient'
import type { Principal } from '../lib/auth'

type LoginProps = {
  onLoggedIn: (principal: Principal) => void
}

const SEED_HINTS: Array<{ tenant: string; accounts: string[] }> = [
  { tenant: '演示企业 A（t1）', accounts: ['admin-a / admin123（管理员）', 'operator-a / operator123（运营）', 'viewer-a / viewer123（访客）'] },
  { tenant: '演示企业 B（t2）', accounts: ['admin-b / admin123（管理员）'] },
]

export function Login({ onLoggedIn }: LoginProps) {
  const [error, setError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  async function handleFinish(values: { username: string; password: string }) {
    setSubmitting(true)
    setError(null)
    try {
      const session = await login(values.username.trim(), values.password)
      onLoggedIn(session.principal)
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : '登录失败')
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
          Atlas 运营体编排平台
        </Typography.Title>
        <Typography.Paragraph type="secondary">登录后进入当前租户的工作台（04 §5.14）</Typography.Paragraph>
        {error && (
          <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }} />
        )}
        <Form layout="vertical" onFinish={handleFinish} requiredMark={false}>
          <Form.Item
            label="用户名"
            name="username"
            rules={[{ required: true, message: '请输入用户名' }]}
          >
            <Input placeholder="admin-a" autoComplete="username" />
          </Form.Item>
          <Form.Item
            label="密码"
            name="password"
            rules={[{ required: true, message: '请输入密码' }]}
          >
            <Input.Password placeholder="admin123" autoComplete="current-password" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block loading={submitting}>
            登录
          </Button>
        </Form>
        <Space orientation="vertical" size={4} style={{ marginTop: 20, width: '100%' }}>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            Demo 种子账号（明文仅用于演示）：
          </Typography.Text>
          {SEED_HINTS.map((group) => (
            <div key={group.tenant}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }} strong>
                {group.tenant}
              </Typography.Text>
              {group.accounts.map((line) => (
                <Typography.Paragraph
                  key={line}
                  type="secondary"
                  style={{ fontSize: 12, marginBottom: 0, paddingLeft: 8 }}
                >
                  {line}
                </Typography.Paragraph>
              ))}
            </div>
          ))}
        </Space>
      </Card>
    </div>
  )
}
