import { useCallback, useEffect, useState } from 'react'
import {
  Button,
  Card,
  Form,
  Input,
  Layout,
  Modal,
  Select,
  Space,
  Switch,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { UserBadge } from '../components/UserBadge'
import type { Principal } from '../lib/auth'
import { useTranslation } from '../locales'
import {
  createUser,
  listUsers,
  resetUserPassword,
  updateUser,
  type UserAccountView,
} from '../lib/apiClient'
import { generatePassword, validateNewUser } from '../lib/users'

const { Content, Header } = Layout

type UsersProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

const ROLE_COLORS = { viewer: 'default', operator: 'blue', admin: 'gold' } as const

export function Users({ principal, onLogout, onBack }: UsersProps) {
  const { t } = useTranslation()
  const [users, setUsers] = useState<UserAccountView[]>([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [editTarget, setEditTarget] = useState<UserAccountView | null>(null)
  const [resetTarget, setResetTarget] = useState<UserAccountView | null>(null)
  const [resetValue, setResetValue] = useState('')
  const [createForm] = Form.useForm()
  const [editForm] = Form.useForm()

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setUsers(await listUsers())
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : t('users.error.loadFailed'))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    // 挂载即拉取用户列表；refresh 内同步置 loading，与 Release/Rollout 弹窗同模式
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void refresh()
  }, [refresh])

  function openCreate() {
    createForm.resetFields()
    setCreateOpen(true)
  }

  async function submitCreate() {
    const values = createForm.getFieldsValue() as {
      username: string
      password: string
      displayName: string
      role: UserAccountView['role']
    }
    const errors = validateNewUser(values)
    if (Object.keys(errors).length) {
      createForm.setFields(
        Object.entries(errors).map(([name, key]) => ({ name, errors: [t(key!)] })),
      )
      return
    }
    try {
      await createUser({
        username: values.username.trim(),
        password: values.password,
        displayName: values.displayName.trim(),
        role: values.role,
      })
      setCreateOpen(false)
      message.success(t('users.message.created'))
      await refresh()
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : t('users.error.createFailed'))
    }
  }

  function openEdit(user: UserAccountView) {
    setEditTarget(user)
    editForm.setFieldsValue({
      displayName: user.displayName,
      role: user.role,
      disabled: user.status === 'disabled',
    })
  }

  async function submitEdit() {
    if (!editTarget) return
    const values = editForm.getFieldsValue() as {
      displayName: string
      role: UserAccountView['role']
      disabled: boolean
    }
    if (!values.displayName?.trim()) {
      editForm.setFields([{ name: 'displayName', errors: [t('users.error.displayNameRequired')] }])
      return
    }
    try {
      await updateUser(editTarget.username, {
        displayName: values.displayName.trim(),
        role: values.role,
        status: values.disabled ? 'disabled' : 'active',
      })
      setEditTarget(null)
      message.success(t('users.message.updated'))
      await refresh()
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : t('users.error.updateFailed'))
    }
  }

  function openReset(user: UserAccountView) {
    const generated = generatePassword()
    setResetTarget(user)
    setResetValue(generated)
  }

  async function submitReset() {
    if (!resetTarget) return
    try {
      await resetUserPassword(resetTarget.username, resetValue)
      setResetTarget(null)
      message.success(t('users.message.reset'))
      await refresh()
    } catch (exc) {
      message.error(exc instanceof Error ? exc.message : t('users.error.resetFailed'))
    }
  }

  const columns = [
    { title: t('users.column.username'), dataIndex: 'username' },
    { title: t('users.column.displayName'), dataIndex: 'displayName' },
    {
      title: t('users.column.role'),
      dataIndex: 'role',
      render: (role: UserAccountView['role']) => <Tag color={ROLE_COLORS[role]}>{t(`role.${role}`)}</Tag>,
    },
    {
      title: t('users.column.status'),
      dataIndex: 'status',
      render: (status: string) =>
        status === 'active' ? t('users.status.active') : t('users.status.disabled'),
    },
    { title: t('users.column.createdAt'), dataIndex: 'createdAt' },
    {
      title: t('users.column.actions'),
      render: (_: unknown, user: UserAccountView) => (
        <Space>
          <Button size="small" onClick={() => openEdit(user)}>
            {t('users.button.edit')}
          </Button>
          <Button size="small" onClick={() => openReset(user)}>
            {t('users.button.resetPassword')}
          </Button>
        </Space>
      ),
    },
  ]

  return (
    <Layout className="page-layout">
      <Header className="page-header" style={{ justifyContent: 'space-between' }}>
        <Typography.Title level={3}>{t('users.title')}</Typography.Title>
        <UserBadge principal={principal} onLogout={onLogout} />
      </Header>
      <Content className="page-content">
        <Space orientation="vertical" size="middle" style={{ width: '100%' }}>
          <Space>
            <Button onClick={onBack}>{t('users.button.back')}</Button>
            <Button type="primary" onClick={openCreate}>
              {t('users.button.create')}
            </Button>
          </Space>
          <Card>
            <Table
              rowKey="username"
              columns={columns}
              dataSource={users}
              loading={loading}
              pagination={false}
            />
          </Card>
        </Space>

        <Modal
          title={t('users.create.title')}
          open={createOpen}
          onOk={submitCreate}
          onCancel={() => setCreateOpen(false)}
          okText={t('users.button.create')}
          cancelText={t('users.button.cancel')}
        >
          <Form layout="vertical" form={createForm}>
            <Form.Item label={t('users.field.username')} name="username">
              <Input />
            </Form.Item>
            <Form.Item label={t('users.field.displayName')} name="displayName">
              <Input />
            </Form.Item>
            <Form.Item label={t('users.field.password')} name="password">
              <Input.Password />
            </Form.Item>
            <Form.Item label={t('users.field.role')} name="role" initialValue="viewer">
              <Select
                options={[
                  { value: 'viewer', label: t('role.viewer') },
                  { value: 'operator', label: t('role.operator') },
                  { value: 'admin', label: t('role.admin') },
                ]}
              />
            </Form.Item>
          </Form>
        </Modal>

        <Modal
          title={t('users.edit.title', { username: editTarget?.username ?? '' })}
          open={editTarget !== null}
          onOk={submitEdit}
          onCancel={() => setEditTarget(null)}
          okText={t('users.button.confirm')}
          cancelText={t('users.button.cancel')}
        >
          <Form layout="vertical" form={editForm}>
            <Form.Item label={t('users.field.displayName')} name="displayName">
              <Input />
            </Form.Item>
            <Form.Item label={t('users.field.role')} name="role">
              <Select
                options={[
                  { value: 'viewer', label: t('role.viewer') },
                  { value: 'operator', label: t('role.operator') },
                  { value: 'admin', label: t('role.admin') },
                ]}
              />
            </Form.Item>
            <Form.Item label={t('users.field.disabled')} name="disabled" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Form>
        </Modal>

        <Modal
          title={t('users.reset.title', { username: resetTarget?.username ?? '' })}
          open={resetTarget !== null}
          onOk={submitReset}
          onCancel={() => setResetTarget(null)}
          okText={t('users.button.confirm')}
          cancelText={t('users.button.cancel')}
        >
          <Typography.Paragraph>{t('users.reset.hint')}</Typography.Paragraph>
          <Input.Password
            value={resetValue}
            onChange={(event) => setResetValue(event.target.value)}
          />
          <Button style={{ marginTop: 8 }} onClick={() => setResetValue(generatePassword())}>
            {t('users.button.generate')}
          </Button>
        </Modal>
      </Content>
    </Layout>
  )
}
