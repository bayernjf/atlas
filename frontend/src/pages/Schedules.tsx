import { useCallback, useEffect, useState } from 'react'
import { Alert, App, Button, Card, Layout, Space, Switch, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  listSchedules,
  runScheduleNow,
  setScheduleEnabled,
  type ScheduleItem,
} from '../lib/apiClient'
import { roleCan, type Principal } from '../lib/auth'
import { UserBadge } from '../components/UserBadge'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type SchedulesPageProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

/** UTC ISO → `2026-09-26 09:45`；刻意不转本地时区（调度口径就是 UTC，转了就等于骗人）。 */
function utcText(iso: string | null): string {
  if (!iso) return ''
  return iso.slice(0, 16).replace('T', ' ')
}

export function Schedules({ principal, onLogout, onBack }: SchedulesPageProps) {
  const { t } = useTranslation('schedules')
  const { message } = App.useApp()
  const [rows, setRows] = useState<ScheduleItem[]>([])
  const [loading, setLoading] = useState(false)
  const [listError, setListError] = useState('')
  const [busyGraph, setBusyGraph] = useState('')
  const canOperate = roleCan(principal.role, 'operate')

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setRows(await listSchedules())
      setListError('')
    } catch (error) {
      // 读失败要说得出声：静默空表会被读成"还没有调度"，那是两种不同的处境。
      setListError((error as Error).message)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void refresh()
  }, [refresh])

  const onToggle = async (row: ScheduleItem, enabled: boolean) => {
    setBusyGraph(row.graphId)
    try {
      const next = await setScheduleEnabled(row.graphId, enabled)
      setRows((current) => current.map((item) => (item.graphId === next.graphId ? next : item)))
      message.success(t(enabled ? 'actions.enabled' : 'actions.disabled'))
    } catch (error) {
      message.error(`${t('actions.toggleFailed')}：${(error as Error).message}`)
      void refresh()
    } finally {
      setBusyGraph('')
    }
  }

  const onRunNow = async (row: ScheduleItem) => {
    setBusyGraph(row.graphId)
    try {
      const result = await runScheduleNow(row.graphId)
      message.success(t('actions.running', { version: result.version }))
      void refresh()
    } catch (error) {
      // 409（同图还在跑）与 404 的中文 detail 直接上屏：它们是答案，不是需要翻译的异常。
      message.error(`${t('actions.runFailed')}：${(error as Error).message}`)
    } finally {
      setBusyGraph('')
    }
  }

  const columns: ColumnsType<ScheduleItem> = [
    { title: t('table.graph'), dataIndex: 'graphId', ellipsis: true },
    {
      title: t('table.cron'),
      dataIndex: 'cron',
      width: 150,
      render: (value: string) => <Typography.Text code>{value}</Typography.Text>,
    },
    {
      title: t('table.version'),
      dataIndex: 'version',
      width: 90,
      render: (value: number) => <Tag>v{value}</Tag>,
    },
    {
      title: t('table.nextFire'),
      dataIndex: 'nextFireAt',
      width: 170,
      render: (value: string | null) => utcText(value) || t('never'),
    },
    {
      title: t('table.lastFired'),
      dataIndex: 'lastFiredAt',
      width: 170,
      render: (value: string | null) => utcText(value) || t('never'),
    },
    { title: t('table.skipped'), dataIndex: 'skipCount', width: 100 },
    {
      title: t('table.enabled'),
      dataIndex: 'enabled',
      width: 90,
      render: (value: boolean, row) => (
        <Switch
          checked={value}
          disabled={!canOperate || busyGraph === row.graphId}
          onChange={(next) => void onToggle(row, next)}
        />
      ),
    },
    {
      title: t('table.actions'),
      width: 120,
      render: (_value, row) =>
        canOperate ? (
          <Button
            size="small"
            loading={busyGraph === row.graphId}
            onClick={() => void onRunNow(row)}
          >
            {t('actions.runNow')}
          </Button>
        ) : null,
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
        <Alert type="info" showIcon message={t('page.description')} style={{ marginBottom: 8 }} />
        <Alert type="warning" showIcon message={t('page.utcNotice')} style={{ marginBottom: 16 }} />
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
            <Button onClick={() => void refresh()} loading={loading}>
              {t('page.refresh')}
            </Button>
          }
        >
          <Table
            rowKey={(row) => `${row.graphId}`}
            columns={columns}
            dataSource={rows}
            loading={loading}
            locale={{ emptyText: t('page.empty') }}
          />
        </Card>
      </Content>
    </Layout>
  )
}
