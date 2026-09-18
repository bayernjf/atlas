import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Modal, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  GateBlockedError,
  publishGraph,
  runReleaseGate,
  type GateCaseRow,
  type GateReport,
} from '../../lib/apiClient'
import { gateConclusion } from '../../lib/release'

const { Text } = Typography

type Props = {
  open: boolean
  graphId: string | null
  onClose: () => void
  /** 发布成功（产新版本）后回调，供父级刷新版本列表 */
  onPublished: (releaseVersion: number) => void
}

/**
 * 发布流门禁 Modal（M9，U59 ②；03 `release_gate`、04 §5.11 末）：
 * 打开即对当前 latest 草稿批量回放 graph_id 匹配的录制用例，逐例 ✓/✗ + note；
 * blocked 禁止发布、total=0 skipped 明示未覆盖但不阻塞。
 */
export function ReleaseModal({ open, graphId, onClose, onPublished }: Props) {
  const [loading, setLoading] = useState(false)
  const [publishing, setPublishing] = useState(false)
  const [report, setReport] = useState<GateReport | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [publishedVersion, setPublishedVersion] = useState<number | null>(null)

  const loadGate = useCallback(async () => {
    if (!graphId) return
    setLoading(true)
    setError(null)
    setReport(null)
    setPublishedVersion(null)
    try {
      setReport(await runReleaseGate(graphId))
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : String(loadError))
    } finally {
      setLoading(false)
    }
  }, [graphId])

  useEffect(() => {
    if (open) void loadGate()
  }, [open, loadGate])

  const doPublish = async () => {
    if (!graphId) return
    setPublishing(true)
    setError(null)
    try {
      // gate:true 以后端为权威再跑一次门禁；blocked → 409 GateBlockedError 不产版本
      const result = await publishGraph(graphId, true)
      setPublishedVersion(result.releaseVersion)
      onPublished(result.releaseVersion)
    } catch (publishError) {
      if (publishError instanceof GateBlockedError) {
        setReport(publishError.report)
      } else {
        setError(publishError instanceof Error ? publishError.message : String(publishError))
      }
    } finally {
      setPublishing(false)
    }
  }

  const conclusion = report ? gateConclusion(report) : null

  const columns: ColumnsType<GateCaseRow> = [
    {
      title: '结果',
      dataIndex: 'matches',
      width: 72,
      render: (matches: boolean) =>
        matches ? <Tag color="green">✓ 一致</Tag> : <Tag color="red">✗ 不匹配</Tag>,
    },
    { title: '用例', dataIndex: 'name' },
    { title: '回放终态', dataIndex: 'replay_status', width: 110 },
    { title: '说明', dataIndex: 'note' },
  ]

  return (
    <Modal
      title={`发布门禁${graphId ? `：${graphId}` : ''}`}
      open={open}
      width={760}
      onCancel={onClose}
      footer={
        <Space>
          <Button onClick={onClose}>关闭</Button>
          {conclusion !== 'blocked' && publishedVersion === null && (
            <Button type="primary" loading={publishing} onClick={doPublish}>
              确认发布为新版本
            </Button>
          )}
        </Space>
      }
    >
      <Space orientation="vertical" size={12} style={{ width: '100%' }}>
        <Text type="secondary">
          对当前草稿批量回放本图录制用例并逐节点比对；存在不匹配用例时拦截发布（不产新版本）。
        </Text>
        {error && <Alert type="error" showIcon message={error} />}
        {publishedVersion !== null && (
          <Alert
            type="success"
            showIcon
            message={`已发布为 v${publishedVersion}（不可变版本）`}
          />
        )}
        {report && conclusion === 'blocked' && (
          <Alert
            type="error"
            showIcon
            message={`门禁未通过：${report.failed}/${report.total} 个用例不匹配，已禁止发布`}
          />
        )}
        {report && conclusion === 'skipped' && (
          <Alert
            type="warning"
            showIcon
            message="本图没有可回放的录制用例（未覆盖），门禁不阻塞发布；建议先录制黄金用例。"
          />
        )}
        {report && conclusion === 'passed' && (
          <Alert
            type="success"
            showIcon
            message={`门禁通过：${report.passed}/${report.total} 个用例全部一致，可发布。`}
          />
        )}
        <Table
          rowKey="case_id"
          size="small"
          loading={loading}
          columns={columns}
          dataSource={report?.cases ?? []}
          pagination={false}
          locale={{ emptyText: loading ? '回放中…' : '暂无匹配用例' }}
        />
      </Space>
    </Modal>
  )
}
