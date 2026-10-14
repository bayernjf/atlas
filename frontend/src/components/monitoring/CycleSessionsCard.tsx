import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Space, Table, Tag, Typography } from 'antd'
import {
  getCycleSession,
  getRunsForCycles,
  stopCycleSession,
  type CycleSessionView,
  type RunListItem,
} from '../../lib/apiClient'
import { useTranslation } from '../../locales'

/**
 * 打包 BN（docs/130 §3.4）：圈会话卡——监控页里看得见"哪些运行属于自走循环、跑到第几圈、
 * 为什么收手"，并能停掉整条会话。
 *
 * 数据源是 `GET /api/runs`（run_store 口径，带 `cycleSeq`/`cycleGroupId` 两列），
 * **不是**监控运行记录那张表（`/api/monitoring/runs` 是另一个 ring 存储，不带圈信息）。
 * 圈会话的活体状态在进程内（单副本），故这张卡只列**当前进程**看得到的会话；
 * 重启后会话消失（docs/130 §5：重启即结束圈、不自动续跑）。
 */
export function CycleSessionsCard({ canOperate }: { canOperate: boolean }) {
  const { t } = useTranslation('monitoring')
  const [runs, setRuns] = useState<RunListItem[]>([])
  // 初值 false：首帧就把表头与说明渲染出来（loading 骨架会把它们整段藏掉，
  // 而"当前没有圈会话"本身就是要让人看到的一句话）；refresh() 随即置 true。
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [detail, setDetail] = useState<Record<string, CycleSessionView>>({})
  const [openGroup, setOpenGroup] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setRuns(await getRunsForCycles(50))
      setError('')
    } catch (err) {
      setError(err instanceof Error ? err.message : t('cycle.error.load'))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect -- 首帧拉取外部 API，setState 均在 await 之后
    void refresh()
  }, [refresh])

  // 只留有圈归属的运行，同一会话取序号最大的一条为代表
  const sessions = Object.values(
    runs
      .filter((run) => run.cycleGroupId)
      .reduce<Record<string, RunListItem>>((acc, run) => {
        const key = run.cycleGroupId as string
        const seen = acc[key]
        if (!seen || (run.cycleSeq ?? 0) > (seen.cycleSeq ?? 0)) acc[key] = run
        return acc
      }, {}),
  )

  const openSession = async (run: RunListItem) => {
    const groupId = run.cycleGroupId as string
    if (openGroup === groupId) {
      setOpenGroup(null)
      return
    }
    try {
      const view = await getCycleSession(run.runId)
      setDetail((prev) => ({ ...prev, [groupId]: view }))
      setOpenGroup(groupId)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('cycle.error.load'))
    }
  }

  const stopSession = async (run: RunListItem) => {
    try {
      const view = await stopCycleSession(run.runId)
      setDetail((prev) => ({ ...prev, [view.groupId]: view }))
    } catch (err) {
      setError(err instanceof Error ? err.message : t('cycle.error.stop'))
    }
  }

  const stateColor = (state: CycleSessionView['state']) =>
    state === 'running'
      ? 'blue'
      : state === 'completed'
        ? 'green'
        : state === 'suspended'
          ? 'orange'
          : 'default'

  return (
    <Card
      title={t('cycle.title')}
      loading={loading}
      extra={
        <Button size="small" onClick={() => void refresh()}>
          {t('cycle.refresh')}
        </Button>
      }
    >
      <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
        {t('cycle.hint')}
      </Typography.Paragraph>
      {error && <Alert type="error" message={error} showIcon style={{ marginBottom: 12 }} />}
      <Table
        rowKey={(run) => run.cycleGroupId as string}
        size="small"
        dataSource={sessions}
        pagination={false}
        locale={{ emptyText: t('cycle.empty') }}
        columns={[
          { title: t('cycle.col.graph'), dataIndex: 'graphId' },
          {
            title: t('cycle.col.seq'),
            dataIndex: 'cycleSeq',
            render: (value: number | undefined) => <Tag color="blue">{t('cycle.seqTag', { n: value ?? 1 })}</Tag>,
          },
          {
            title: t('cycle.col.state'),
            key: 'state',
            render: (_: unknown, run: RunListItem) => {
              const view = detail[run.cycleGroupId as string]
              if (!view) return <Typography.Text type="secondary">—</Typography.Text>
              return (
                <Space size={4}>
                  <Tag color={stateColor(view.state)}>{t(`cycle.state.${view.state}`)}</Tag>
                  {view.stopReason && (
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {view.stopReason}
                    </Typography.Text>
                  )}
                </Space>
              )
            },
          },
          {
            title: t('cycle.col.tokens'),
            key: 'tokens',
            render: (_: unknown, run: RunListItem) => {
              const view = detail[run.cycleGroupId as string]
              return view ? view.tokensSpent : '—'
            },
          },
          {
            title: t('cycle.col.actions'),
            key: 'actions',
            render: (_: unknown, run: RunListItem) => {
              const view = detail[run.cycleGroupId as string]
              const live = view ? view.state === 'running' : true
              return (
                <Space size={4}>
                  <Button size="small" onClick={() => void openSession(run)}>
                    {openGroup === run.cycleGroupId ? t('cycle.collapse') : t('cycle.expand')}
                  </Button>
                  {canOperate && (
                    <Button size="small" danger disabled={!live} onClick={() => void stopSession(run)}>
                      {t('cycle.stop')}
                    </Button>
                  )}
                </Space>
              )
            },
          },
        ]}
        expandable={{
          expandedRowRender: (run: RunListItem) => {
            const view = detail[run.cycleGroupId as string]
            if (!view) return null
            return (
              <Space direction="vertical" size={4} style={{ fontSize: 12 }}>
                <Typography.Text>
                  {t('cycle.detail.cycles', { done: view.seq, max: view.maxIterations })}
                </Typography.Text>
                <Typography.Text>
                  {t('cycle.detail.wallClock', {
                    used: view.elapsedSeconds,
                    max: view.maxWallClockSeconds,
                  })}
                </Typography.Text>
                <Typography.Text>
                  {t('cycle.detail.tokens', { used: view.tokensSpent, max: view.maxTokens })}
                </Typography.Text>
                <Typography.Text type="secondary">{view.groupId}</Typography.Text>
              </Space>
            )
          },
          rowExpandable: (run: RunListItem) => Boolean(detail[run.cycleGroupId as string]),
        }}
      />
    </Card>
  )
}
