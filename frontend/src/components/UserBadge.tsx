import { useState } from 'react'
import { Button, Dropdown, Form, Input, Modal, Space, Tag, Typography, message } from 'antd'
import { type Principal } from '../lib/auth'
import { SUPPORTED_LOCALES, useTranslation, type Locale } from '../locales'
import { ApiError, changePassword } from '../lib/apiClient'
import { validateChangePassword } from '../lib/users'

/** 语言在菜单中显示各自原名，不随当前语言翻译（docs/57 §4）。 */
const LOCALE_LABELS: Record<Locale, string> = {
  'zh-CN': '中文',
  'en-US': 'English',
}

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
  const { t, i18n } = useTranslation()
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
      // 项目级审查发现 #7（docs/125 §3.5）：按结构化 code 分支，不匹配中文文案。
      if (exc instanceof ApiError && exc.code === 'AUTH_OLD_PASSWORD_INVALID') {
        form.setFields([{ name: 'oldPassword', errors: [t('users.error.oldPasswordWrong')] }])
      } else {
        message.error(exc instanceof Error ? exc.message : t('users.error.changeFailed'))
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
            { type: 'divider' },
            {
              key: 'language-group',
              type: 'group',
              label: t('language.label'),
              children: SUPPORTED_LOCALES.map((loc) => ({
                key: `lang-${loc}`,
                label: LOCALE_LABELS[loc],
              })),
            },
          ],
          selectable: true,
          selectedKeys: [`lang-${i18n.language}`],
          onClick: ({ key }) => {
            if (key === 'change-password') openChange()
            if (key === 'logout') onLogout()
            if (key.startsWith('lang-')) i18n.changeLanguage(key.slice(5) as Locale)
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
