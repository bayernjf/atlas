import { useState } from 'react'
import { Alert, Button, Form, Input, Modal, Typography } from 'antd'
import { changePassword, logout as logoutApi } from '../lib/apiClient'
import { useTranslation } from '../locales'
import { validateChangePassword } from '../lib/users'

type ForcePasswordChangeProps = {
  onDone: () => void
  onLoggedOut: () => void
}

/**
 * 打包 AV（docs/95）：首登强制改密框——**不可关闭**。
 *
 * 没有取消按钮、点遮罩不关、Esc 不关：一个能关掉的强制框等于没有强制。之所以敢挡成这样，
 * 是因为 auth 三条路由在未改密时始终可达（U1143）——`/api/auth/change-password` 就是出口，
 * 而改密成功保留当前会话（U1143 第二条），所以这里不需要"稍后处理"。
 *
 * 留一条退出登录：忘了引导口令的人该找部署方轮换 `ATLAS_ADMIN_BOOTSTRAP_PASSWORD`，
 * 而不是被视觉上困在一个无法完成的表单里。退出只是登出，不放开任何业务端点。
 */
export function ForcePasswordChange({ onDone, onLoggedOut }: ForcePasswordChangeProps) {
  const { t } = useTranslation()
  const [form] = Form.useForm()
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function submit() {
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
    setSubmitting(true)
    setError(null)
    try {
      await changePassword(values.oldPassword, values.newPassword)
      onDone()
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : t('users.error.changeFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  async function signOut() {
    setSubmitting(true)
    try {
      await logoutApi()
    } finally {
      setSubmitting(false)
      onLoggedOut()
    }
  }

  return (
    <Modal
      open
      title={t('users.forceChange.title')}
      closable={false}
      maskClosable={false}
      keyboard={false}
      footer={
        <Button type="primary" loading={submitting} onClick={submit} block>
          {t('users.forceChange.submit')}
        </Button>
      }
    >
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message={t('users.forceChange.body')}
      />
      {error && (
        <Alert type="error" showIcon message={error} style={{ marginBottom: 12 }} />
      )}
      <Form layout="vertical" form={form} requiredMark={false}>
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
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        <Button type="link" size="small" onClick={signOut} style={{ padding: 0 }}>
          {t('users.forceChange.signOut')}
        </Button>
      </Typography.Text>
    </Modal>
  )
}
