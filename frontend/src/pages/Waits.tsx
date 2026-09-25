import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  App,
  Button,
  Card,
  Input,
  Layout,
  Modal,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  broadcastWaitEvent,
  listTasks,
  listWaits,
  signalWait,
  type PendingWaitItem,
  type TaskEnvelopeItem,
} from '../lib/apiClient'
import { roleCan, type Principal } from '../lib/auth'
import { UserBadge } from '../components/UserBadge'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type WaitsPageProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

/** 解析 JSON 对象文本；非法返回 null（供 Modal 校验）。 */
function parsePayloadText(text: string): Record<string, unknown> | null {
  try {
    const value = JSON.parse(text)
    return value && typeof value === 'object' && !Array.isArray(value) ? value : null
  } catch {
    return null
  }
}

const TASK_STATE_COLORS: Record<string, string> = {
  pending: 'gold',
  accepted: 'blue',
  running: 'geekblue',
  done: 'green',
  failed: 'red',
  timeout: 'orange',
}

export function Waits({ principal, onLogout, onBack }: WaitsPageProps) {
  const { t } = useTranslation('waits')
  const { message } = App.useApp()
  const [waits, setWaits] = useState<PendingWaitItem[]>([])
  const [tasks, setTasks] = useState<TaskEnvelopeItem[]>([])
  const [tab, setTab] = useState('waits')
  const [broadcastKey, setBroadcastKey] = useState('')
  const [broadcastPayload, setBroadcastPayload] = useState('')
  const [broadcastOpen, setBroadcastOpen] = useState(false)
  const [signalTarget, setSignalTarget] = useState<PendingWaitItem | null>(null)
  const [signalPayload, setSignalPayload] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    try {
      const [waitItems, taskItems] = await Promise.all([listWaits(), listTasks(50)])
      setWaits(waitItems)
      setTasks(taskItems)
    } catch (error) {
      message.error(String(error))
    }
  }, [message])

  useEffect(() => {
    refresh()
    const timer = window.setInterval(refresh, 10_000)
    return () => window.clearInterval(timer)
  }, [refresh])

  async function submitBroadcast() {
    const payload = parsePayloadText(broadcastPayload)
    if (!payload) {
      message.error(t('payloadInvalid'))
      return
    }
    setBusy(true)
    try {
      const result = await broadcastWaitEvent(broadcastKey.trim(), payload)
      message.success(
        `${t('released', { count: result.released })}${result.queued ? t('queued') : ''}`,
      )
      setBroadcastOpen(false)
      setBroadcastKey('')
      setBroadcastPayload('')
      await refresh()
    } catch (error) {
      message.error(String(error))
    } finally {
      setBusy(false)
    }
  }

  async function submitSignal() {
    if (!signalTarget) return
    const payload = parsePayloadText(signalPayload)
    if (!payload) {
      message.error(t('payloadInvalid'))
      return
    }
    setBusy(true)
    try {
      const result = await signalWait(signalTarget.token, payload)
      message.success(t('sent'))
      if (result.released) {
        setSignalTarget(null)
        setSignalPayload('')
        await refresh()
      }
    } catch (error) {
      message.error(String(error))
    } finally {
      setBusy(false)
    }
  }

  const waitColumns: ColumnsType<PendingWaitItem> = [
    {
      title: t('eventKey'),
      dataIndex: 'eventKey',
      render: (value: string, item) =>
        item.eventWaitMode === 'all' ? (
          <Tag color="purple">{item.eventKeys?.join(' + ')}</Tag>
        ) : (
          <Tag>{value}</Tag>
        ),
    },
    { title: t('nodeId'), dataIndex: 'nodeId', width: 110 },
    { title: t('graphId'), dataIndex: 'graphId', width: 140 },
    { title: t('timeout'), dataIndex: 'timeoutSeconds', width: 80, render: (v: number) => `${v}s` },
    { title: t('deadline'), dataIndex: 'deadlineAt', width: 180 },
    { title: t('token'), dataIndex: 'token', ellipsis: true },
    {
      title: '',
      key: 'actions',
      width: 100,
      render: (_, item) => (
        <Button
          size="small"
          onClick={() => {
            setSignalTarget(item)
            setSignalPayload('')
          }}
        >
          {t('signal')}
        </Button>
      ),
    },
  ]

  const taskColumns: ColumnsType<TaskEnvelopeItem> = [
    { title: t('type'), dataIndex: 'type', width: 120 },
    {
      title: t('state'),
      dataIndex: 'state',
      width: 100,
      render: (value: TaskEnvelopeItem['state']) => (
        <Tag color={TASK_STATE_COLORS[value] ?? 'default'}>{value}</Tag>
      ),
    },
    { title: t('assignee'), dataIndex: 'assignee', width: 100 },
    { title: t('runId'), dataIndex: 'runId', width: 160, ellipsis: true },
    { title: t('attempt'), dataIndex: 'attempt', width: 70 },
    {
      title: t('colResult'),
      dataIndex: 'result',
      ellipsis: true,
      render: (value: Record<string, unknown>) =>
        value && Object.keys(value).length > 0 ? JSON.stringify(value) : '—',
    },
  ]

  return (
    <Layout className="page-layout">
      <Header className="page-header" style={{ justifyContent: 'space-between' }}>
        <Space>
          <Button onClick={onBack}>←</Button>
          <Typography.Title level={3} style={{ margin: 0 }}>
            {t('title')}
          </Typography.Title>
        </Space>
        <UserBadge principal={principal} onLogout={onLogout} />
      </Header>
      <Content className="page-content">
        <Alert type="info" showIcon message={t('description')} style={{ marginBottom: 16 }} />
        <Card
          extra={
            <Space>
              <Button size="small" onClick={refresh}>
                {t('refresh')}
              </Button>
              <Button
                size="small"
                type="primary"
                disabled={!roleCan(principal.role, 'operate')}
                onClick={() => setBroadcastOpen(true)}
              >
                {t('broadcast')}
              </Button>
            </Space>
          }
        >
          <Tabs
            activeKey={tab}
            onChange={setTab}
            items={[
              {
                key: 'waits',
                label: `${t('waitsTab')} (${waits.length})`,
                children: (
                  <Table<PendingWaitItem>
                    rowKey="token"
                    size="small"
                    dataSource={waits}
                    columns={waitColumns}
                    locale={{ emptyText: t('empty') }}
                  />
                ),
              },
              {
                key: 'tasks',
                label: `${t('tasksTab')} (${tasks.length})`,
                children: (
                  <Table<TaskEnvelopeItem>
                    rowKey="taskId"
                    size="small"
                    dataSource={tasks}
                    columns={taskColumns}
                    locale={{ emptyText: t('empty') }}
                  />
                ),
              },
            ]}
          />
        </Card>

        <Modal
          open={broadcastOpen}
          title={t('broadcast')}
          onCancel={() => setBroadcastOpen(false)}
          onOk={submitBroadcast}
          okButtonProps={{ disabled: busy || !roleCan(principal.role, 'operate') }}
          destroyOnClose
        >
          <Space orientation="vertical" style={{ width: '100%' }}>
            <Typography.Text type="secondary">{t('broadcastHint')}</Typography.Text>
            <Input
              placeholder={t('eventKey')}
              value={broadcastKey}
              onChange={(event) => setBroadcastKey(event.target.value)}
            />
            <Input.TextArea
              rows={4}
              placeholder={t('payloadPlaceholder')}
              value={broadcastPayload}
              onChange={(event) => setBroadcastPayload(event.target.value)}
            />
          </Space>
        </Modal>

        <Modal
          open={signalTarget !== null}
          title={`${t('signal')} · ${signalTarget?.eventKey ?? ''}`}
          onCancel={() => setSignalTarget(null)}
          onOk={submitSignal}
          okButtonProps={{ disabled: busy || !roleCan(principal.role, 'operate') }}
          destroyOnClose
        >
          <Input.TextArea
            rows={4}
            placeholder={t('payloadPlaceholder')}
            value={signalPayload}
            onChange={(event) => setSignalPayload(event.target.value)}
          />
        </Modal>
      </Content>
    </Layout>
  )
}
