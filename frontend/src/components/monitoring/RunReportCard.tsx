import { useCallback, useEffect, useState } from 'react'
import { Button, Card, Segmented, Select, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  getRunReport,
  runReportExportUrl,
  type RunReportBucket,
} from '../../lib/apiClient'
import { useTranslation } from '../../locales'

const { Text } = Typography

const DAY_OPTIONS = [7, 14, 30, 90]

/**
 * 运行报表卡（docs/113 打包 AG）：窗口内运行按天/版本聚合（计数、成功率、耗时 avg/p50/p95），
 * 支持导出 CSV/JSON；认证走 httpOnly Cookie，导出链接浏览器自动带凭证。
 */
export function RunReportCard() {
  const { t } = useTranslation('monitoring')
  const [days, setDays] = useState(7)
  const [groupBy, setGroupBy] = useState<'day' | 'version'>('day')
  const [buckets, setBuckets] = useState<RunReportBucket[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const report = await getRunReport({ days, groupBy })
      setBuckets(report.buckets)
      setError(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [days, groupBy])

  useEffect(() => {
    let cancelled = false
    getRunReport({ days, groupBy })
      .then((report) => {
        if (!cancelled) setBuckets(report.buckets)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [days, groupBy])

  const downloadReport = (format: 'csv' | 'json') => {
    window.open(runReportExportUrl({ days, groupBy, format }), '_blank')
  }

  const columns: ColumnsType<RunReportBucket> = [
    { title: t('runReport.col.key'), dataIndex: 'key', width: 150 },
    { title: t('runReport.col.total'), dataIndex: 'total', width: 90 },
    { title: t('runReport.col.completed'), dataIndex: 'completed', width: 90 },
    {
      title: t('runReport.col.error'),
      dataIndex: 'error',
      width: 90,
      render: (value: number) => (value > 0 ? <Tag color="red">{value}</Tag> : 0),
    },
    { title: t('runReport.col.cancelled'), dataIndex: 'cancelled', width: 90 },
    {
      title: t('runReport.col.successRate'),
      dataIndex: 'success_rate',
      width: 110,
      render: (rate: number) => (
        <Text type={rate >= 0.95 ? 'success' : rate >= 0.8 ? 'warning' : 'danger'}>
          {(rate * 100).toFixed(1)}%
        </Text>
      ),
    },
    {
      title: t('runReport.col.avg'),
      dataIndex: 'duration_avg_ms',
      width: 110,
      render: (value: number) => value.toFixed(0),
    },
    { title: t('runReport.col.p50'), dataIndex: 'duration_p50_ms', width: 100,
      render: (value: number) => value.toFixed(0) },
    { title: t('runReport.col.p95'), dataIndex: 'duration_p95_ms', width: 100,
      render: (value: number) => value.toFixed(0) },
  ]

  return (
    <Card
      title={t('runReport.card.title')}
      extra={
        <Space wrap>
          <Segmented
            value={groupBy}
            onChange={(value) => setGroupBy(value as 'day' | 'version')}
            options={[
              { label: t('runReport.group.day'), value: 'day' },
              { label: t('runReport.group.version'), value: 'version' },
            ]}
          />
          <Select
            value={days}
            onChange={setDays}
            style={{ width: 110 }}
            options={DAY_OPTIONS.map((value) => ({
              label: t('runReport.days', { value }),
              value,
            }))}
          />
          <Button size="small" onClick={refresh} loading={loading}>
            {t('header.refresh')}
          </Button>
          <Button size="small" onClick={() => downloadReport('csv')}>
            {t('runReport.export.csv')}
          </Button>
          <Button size="small" onClick={() => downloadReport('json')}>
            {t('runReport.export.json')}
          </Button>
        </Space>
      }
    >
      {error && <Text type="danger">{error}</Text>}
      <Table<RunReportBucket>
        size="small"
        rowKey="key"
        loading={loading}
        pagination={false}
        dataSource={buckets}
        columns={columns}
        scroll={{ x: 940 }}
      />
    </Card>
  )
}
