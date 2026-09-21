import { useState } from 'react'
import { Button, Dropdown, Form, Input, Modal, Space, Tag, Typography, message } from 'antd'
import { type Principal } from '../lib/auth'
import { useTranslation } from '../locales'
import { changePassword } from '../lib/apiClient'
import { validateChangePassword } from '../lib/users'

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
  const [changeOpen, setChangeOpen] = useState(false)
  const [form] = Form.useForm()

  function openChange() {
    form.resetFields()
    setChangeOpen(true)
  }

  async function submitChange() {
    const values = form.getFieldsValue() as {
      oldPassword: string
      newPassword: string
      confirmPassword: string
    }
    const errors = validateChangePassword(values)
    if (Object.keys(errors).length) {
      form.setFields(
        Object.entries(errors).map(([name, key]) => ({ name, errors: [t(key!)] })),
      )
      return
    }
    try {
      await changePassword(values.oldPassword, values.newPassword)
      setChangeOpen(false)
      message.success(t('users.message.passwordChanged'))
    } catch (exc) {
      const detail = exc instanceof Error ? exc.message : t('users.error.changeFailed')
      if (detail === '原密码错误') {
        form.setFields([{ name: 'oldPassword', errors: [t('users.error.oldPasswordWrong')] }])
      } else {
        message.error(detail)
      }
    }
  }

  return (
    <Space size="middle">
      <Typography.Text strong style={{ color: 'rgba(255,255,255,0.85)' }}>
        {principal.tenant_name} · {principal.display_name}
      </Typography.Text>
      <Tag color={ROLE_COLORS[principal.role]}>{t(`role.${principal.role}`)}</Tag>
      <Dropdown
        menu={{
          items: [
            { key: 'change-password', label: t('users.menu.changePassword') },
            { key: 'logout', label: t('auth.session.logout'), danger: true },
          ],
          onClick: ({ key }) => {
            if (key === 'change-password') openChange()
            if (key === 'logout') onLogout()
          },
        }}
      >
        <Button size="small" ghost>
          {t('users.menu.account')}
        </Button>
      </Dropdown>

      <Modal
        title={t('users.changePassword.title')}
        open={changeOpen}
        onOk={submitChange}
        onCancel={() => setChangeOpen(false)}
        okText={t('users.button.confirm')}
        cancelText={t('users.button.cancel')}
      >
        <Form layout="vertical" form={form}>
          <Form.Item label={t('users.field.oldPassword')} name="oldPassword">
            <Input.Password autoComplete="current-password" />
          </Form.Item>
          <Form.Item label={t('users.field.newPassword')} name="newPassword">
            <Input.Password autoComplete="new-password" />
          </Form.Item>
          <Form.Item label={t('users.field.confirmPassword')} name="confirmPassword">
            <Input.Password autoComplete="new-password" />
          </Form.Item>
        </Form>
      </Modal>
    </Space>
  )
}
