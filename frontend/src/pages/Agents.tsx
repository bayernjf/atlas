import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  App,
  Button,
  Card,
  Drawer,
  Form,
  Input,
  Layout,
  Modal,
  Popconfirm,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  createAgent,
  deleteAgent,
  getAgentRuns,
  listAgents,
  updateAgent,
  type AgentRunItem,
  type AgentRunsView,
  type AgentView,
} from '../lib/apiClient'
import { roleCan, type Principal } from '../lib/auth'
import { UserBadge } from '../components/UserBadge'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type AgentsPageProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

/** ISO → `2026-10-14 09:45`（与 Schedules 同款的轻量截断展示，不转时区）。 */
function isoText(iso: string | null | undefined): string {
  return iso ? iso.slice(0, 19).replace('T', ' ') : ''
}

// 打包 BO（docs/131 D74）：可管理 agent 页——列表（read）／启停（operate）／
// 新建删除（administer）／执行日记＋token 汇总（read，走 LLM 用量账）。
export function Agents({ principal, onLogout, onBack }: AgentsPageProps) {
  const { t } = useTranslation('agents')
  const { message } = App.useApp()
  const [rows, setRows] = useState<AgentView[]>([])
  const [loading, setLoading] = useState(false)
  const [listError, setListError] = useState('')
  const [busyRow, setBusyRow] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm<{ name: string }>()
  const [diary, setDiary] = useState<AgentRunsView | null>(null)
  const [diaryLoading, setDiaryLoading] = useState(false)

  const canOperate = roleCan(principal.role, 'operate')
  const canAdmin = roleCan(principal.role, 'administer')

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setRows(await listAgents())
      setListError('')
    } catch (error) {
      setListError((error as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    void refresh()
  }, [refresh])
  /* oxlint-enable react/set-state-in-effect */

  const handleCreate = async () => {
    const values = await form.validateFields()
    setCreating(true)
    try {
      await createAgent({ name: values.name.trim() })
      message.success(t('actions.created'))
      setCreateOpen(false)
      form.resetFields()
      void refresh()
    } catch (error) {
      message.error(`${t('error.create')}：${(error as Error).message}`)
    } finally {
      setCreating(false)
    }
  }

  const handleToggle = async (agent: AgentView, enabled: boolean) => {
    setBusyRow(agent.id)
    try {
      const next = await updateAgent(agent.id, { enabled })
      setRows((current) => current.map((item) => (item.id === next.id ? next : item)))
      message.success(t(enabled ? 'actions.enabled' : 'actions.disabled'))
    } catch (error) {
      message.error(`${t('actions.toggleFailed')}：${(error as Error).message}`)
      void refresh()
    } finally {
      setBusyRow('')
    }
  }

  const handleDelete = async (agent: AgentView) => {
    setBusyRow(agent.id)
    try {
      await deleteAgent(agent.id)
      message.success(t('actions.deleted'))
      void refresh()
    } catch (error) {
      message.error(`${t('error.delete')}：${(error as Error).message}`)
    } finally {
      setBusyRow('')
    }
  }

  const openDiary = async (agent: AgentView) => {
    setDiaryLoading(true)
    try {
      setDiary(await getAgentRuns(agent.id))
    } catch (error) {
      message.error(`${t('diary.loadError')}：${(error as Error).message}`)
    } finally {
      setDiaryLoading(false)
    }
  }

  const statusColor = (status: string) =>
    status === 'completed'
      ? 'success'
      : status === 'failed'
        ? 'error'
        : status === 'running'
          ? 'processing'
          : 'default'

  const columns: ColumnsType<AgentView> = [
    { title: t('table.name'), dataIndex: 'name', ellipsis: true },
    {
      title: t('table.id'),
      dataIndex: 'id',
      ellipsis: true,
      render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: t('table.enabled'),
      dataIndex: 'enabled',
      width: 90,
      render: (value: boolean, row) => (
        <Switch
          checked={value}
          disabled={!canOperate || busyRow === row.id}
          onChange={(next) => void handleToggle(row, next)}
        />
      ),
    },
    {
      title: t('table.createdAt'),
      dataIndex: 'createdAt',
      width: 170,
      render: (value: string) => isoText(value),
    },
    {
      title: t('table.actions'),
      width: 220,
      render: (_value, row) => (
        <Space size={4} wrap>
          <Button size="small" onClick={() => void openDiary(row)}>
            {t('actions.diary')}
          </Button>
          {canAdmin && (
            <Popconfirm
              title={t('deleteConfirm.title')}
              description={t('deleteConfirm.description')}
              okText={t('common:button.delete')}
              cancelText={t('common:button.cancel')}
              okButtonProps={{ danger: true }}
              onConfirm={() => void handleDelete(row)}
            >
              <Button size="small" danger loading={busyRow === row.id}>
                {t('common:button.delete')}
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  const runColumns: ColumnsType<AgentRunItem> = [
    {
      title: t('diary.colRunId'),
      dataIndex: 'runId',
      ellipsis: true,
      render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: t('diary.colGraph'),
      dataIndex: 'graphId',
      ellipsis: true,
      render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: t('diary.colStatus'),
      dataIndex: 'status',
      width: 110,
      render: (value: string) => <Tag color={statusColor(value)}>{value}</Tag>,
    },
    {
      title: t('diary.colStarted'),
      dataIndex: 'startedAt',
      width: 170,
      render: (value: string) => isoText(value),
    },
  ]

  return (
    <Layout className="page-layout">
      <Header className="page-header" style={{ justifyContent: 'space-between' }}>
        <Space>
          <Button onClick={onBack}>←</Button>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {t('page.title')}
          </Typography.Title>
        </Space>
        <UserBadge principal={principal} onLogout={onLogout} />
      </Header>
      <Content className="page-content">
        <Alert type="info" showIcon message={t('page.description')} style={{ marginBottom: 16 }} />
        {listError && (
          <Alert
            type="error"
            showIcon
            message={`${t('page.loadError')}：${listError}`}
            style={{ marginBottom: 16 }}
          />
        )}
        <Card
          extra={
            <Space size={8} wrap>
              {canAdmin && (
                <Button type="primary" onClick={() => setCreateOpen(true)}>
                  {t('actions.create')}
                </Button>
              )}
              <Button onClick={() => void refresh()} loading={loading}>
                {t('page.refresh')}
              </Button>
            </Space>
          }
        >
          <Table<AgentView>
            rowKey="id"
            size="small"
            loading={loading}
            dataSource={rows}
            columns={columns}
            pagination={false}
            locale={{ emptyText: t('page.empty') }}
          />
        </Card>
      </Content>

      <Modal
        title={t('createModal.title')}
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        onOk={() => void handleCreate()}
        okText={t('common:button.confirm')}
        cancelText={t('common:button.cancel')}
        confirmLoading={creating}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" style={{ marginTop: 12 }}>
          <Form.Item
            name="name"
            label={t('createModal.name')}
            rules={[{ required: true, whitespace: true, message: t('createModal.nameRequired') }]}
          >
            <Input placeholder={t('createModal.namePlaceholder')} maxLength={128} />
          </Form.Item>
        </Form>
      </Modal>

      <Drawer
        title={diary ? `${t('diary.title')} · ${diary.agent.name}` : t('diary.title')}
        open={diary !== null || diaryLoading}
        onClose={() => setDiary(null)}
        width={760}
        loading={diaryLoading && diary === null}
      >
        {diary && (
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <Card size="small">
              <Typography.Text type="secondary">{t('diary.tokens')}</Typography.Text>
              <Space size={32} wrap style={{ marginTop: 8 }}>
                <Statistic
                  title={t('diary.calls')}
                  value={diary.tokenSummary.calls}
                  suffix={t('diary.tokensUnit')}
                />
                <Statistic title={t('diary.prompt')} value={diary.tokenSummary.promptTokens} />
                <Statistic
                  title={t('diary.completion')}
                  value={diary.tokenSummary.completionTokens}
                />
                <Statistic title={t('diary.total')} value={diary.tokenSummary.totalTokens} />
              </Space>
            </Card>
            <Table<AgentRunItem>
              rowKey="runId"
              size="small"
              dataSource={diary.items}
              columns={runColumns}
              pagination={false}
              locale={{ emptyText: t('diary.empty') }}
            />
          </Space>
        )}
      </Drawer>
    </Layout>
  )
}
