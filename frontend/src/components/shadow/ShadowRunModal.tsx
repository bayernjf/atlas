import { useMemo, useState, type ReactNode } from 'react'
import {
  Alert,
  Button,
  Descriptions,
  Empty,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import {
  createShadowRun,
  type ShadowDecision,
  type ShadowRun,
  type ToolIntent,
} from '../../lib/apiClient'
import {
  INTENT_KIND_COLORS,
  HUMAN_ACTION_VALUES,
  autoActionLabel,
  comparisonVerdict,
  formatParameters,
  intentKind,
  parseShadowInputs,
} from '../../lib/shadow'
import { useTranslation } from '../../locales'

const { Text, Paragraph } = Typography
const { TextArea } = Input

type Props = {
  open: boolean
  graphId: string | null
  onClose: () => void
}

const VERDICT_COLORS = {
  consistent: 'green',
  mismatch: 'red',
  pending: 'default',
} as const

/**
 * D26 影子运行入口（docs/33 §3）：对已保存图的 latest 草稿旁路跑一遍完整决策链路，
 * READ 透传、写能力短路为 SHADOW_DRY_RUN，不写 RunRecord/不触发告警门控/不触达写适配器；
 * 可选在发起时直接带上人工实际处理，立即得到「系统本会怎么做 vs 人工实际」对比。
 */
export function ShadowRunModal({ open, graphId, onClose }: Props) {
  const { t } = useTranslation('monitoring')
  const [inputsText, setInputsText] = useState('')
  const [humanAction, setHumanAction] = useState<string | undefined>(undefined)
  const [humanNote, setHumanNote] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)
  const [run, setRun] = useState<ShadowRun | null>(null)

  // 关闭动画结束后重置（事件回调，非 effect；首次挂载即初始值），保证每次打开是干净表单。
  const resetForm = () => {
    setInputsText('')
    setHumanAction(undefined)
    setHumanNote('')
    setSubmitting(false)
    setFormError(null)
    setRun(null)
  }

  const inputsHelp = useMemo(() => {
    const parsed = parseShadowInputs(inputsText)
    return parsed.ok ? null : t(parsed.error)
  }, [inputsText, t])

  async function submit() {
    if (!graphId) return
    const parsed = parseShadowInputs(inputsText)
    if (!parsed.ok) {
      setFormError(t(parsed.error))
      return
    }
    setFormError(null)
    setSubmitting(true)
    try {
      const body: { inputs?: Record<string, unknown>; human_outcome?: { action: string; note?: string } } = {
        inputs: parsed.value,
      }
      if (humanAction) {
        body.human_outcome = { action: humanAction }
        if (humanNote.trim()) body.human_outcome.note = humanNote.trim()
      }
      setRun(await createShadowRun(graphId, body))
    } catch (error) {
      setFormError(error instanceof Error ? error.message : String(error))
    } finally {
      setSubmitting(false)
    }
  }

  const renderAction = (action: string | null): ReactNode => {
    if (!action) return '—'
    const label = autoActionLabel(action)
    const text = label && label.startsWith('shadow.') ? t(label) : label
    return <Text strong>{text}</Text>
  }

  const decisionColumns: ColumnsType<ShadowDecision> = [
    { title: t('shadow.col.node'), dataIndex: 'node_id', key: 'node_id' },
    {
      title: t('shadow.col.nodeType'),
      dataIndex: 'node_type',
      key: 'node_type',
      render: (v: string) => <Tag>{v}</Tag>,
    },
    {
      title: t('shadow.col.target'),
      dataIndex: 'target',
      key: 'target',
      render: (v: string | null) => v ?? '—',
    },
  ]

  const intentColumns: ColumnsType<ToolIntent> = [
    { title: t('shadow.col.node'), dataIndex: 'node_id', key: 'node_id' },
    { title: t('shadow.col.tool'), dataIndex: 'tool', key: 'tool', className: 'font-mono' },
    {
      title: t('shadow.col.permission'),
      dataIndex: 'permission',
      key: 'permission',
      render: (v: string | null) => (v ? <Tag color="geekblue">{v}</Tag> : '—'),
    },
    {
      title: t('shadow.col.intent'),
      key: 'intent',
      render: (_, record) => {
        const kind = intentKind(record)
        return (
          <Space direction="vertical" size={0}>
            <Tag color={INTENT_KIND_COLORS[kind]}>{t(`shadow.intent.${kind}`)}</Tag>
            <Text type="secondary" style={{ fontSize: 12 }}>
              {record.action_status}
            </Text>
          </Space>
        )
      },
    },
    {
      title: t('shadow.col.parameters'),
      dataIndex: 'parameters',
      key: 'parameters',
      render: (_: unknown, record) => {
        const params = formatParameters(record.parameters)
        return params ? (
          <Paragraph>
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{params}</pre>
          </Paragraph>
        ) : (
          '—'
        )
      },
    },
  ]

  const verdict = run ? comparisonVerdict(run.comparison.match) : null

  return (
    <Modal
      title={t('shadow.modal.title')}
      open={open}
      onCancel={onClose}
      afterClose={resetForm}
      width={960}
      footer={
        <Space>
          <Button onClick={onClose}>{t('common:button.cancel')}</Button>
          <Button type="primary" loading={submitting} onClick={submit} disabled={!graphId}>
            {t('shadow.modal.submit')}
          </Button>
        </Space>
      }
    >
      <Alert type="info" showIcon message={t('shadow.modal.hint')} style={{ marginBottom: 16 }} />
      <Form layout="vertical">
        <Form.Item
          label={t('shadow.modal.inputsLabel')}
          validateStatus={inputsHelp ? 'error' : undefined}
          help={inputsHelp ?? t('shadow.modal.inputsHelp')}
        >
          <TextArea
            rows={3}
            value={inputsText}
            onChange={(e) => setInputsText(e.target.value)}
            placeholder='{"order_id":"12345"}'
            className="font-mono"
          />
        </Form.Item>
        <Space size={16} align="start" wrap>
          <Form.Item label={t('shadow.modal.humanLabel')} style={{ marginBottom: 8 }}>
            <Select
              allowClear
              style={{ width: 220 }}
              value={humanAction}
              onChange={(v) => setHumanAction(v)}
              placeholder={t('shadow.modal.humanPlaceholder')}
              options={HUMAN_ACTION_VALUES.map((v) => ({
                value: v,
                label: t(`shadow.action.${v === 'refunded' ? 'refunded' : 'humanReview'}`),
              }))}
            />
          </Form.Item>
          {humanAction && (
            <Form.Item label={t('shadow.modal.noteLabel')} style={{ marginBottom: 8 }}>
              <Input
                style={{ width: 280 }}
                value={humanNote}
                onChange={(e) => setHumanNote(e.target.value)}
                placeholder={t('shadow.modal.notePlaceholder')}
              />
            </Form.Item>
          )}
        </Space>
      </Form>

      {formError && <Alert type="error" showIcon message={formError} style={{ marginBottom: 16 }} />}

      {run && (
        <>
          {run.status === 'error' && (
            <Alert type="error" showIcon message={run.error ?? t('shadow.result.error')} style={{ marginBottom: 16 }} />
          )}
          <Descriptions
            size="small"
            bordered
            column={1}
            style={{ marginBottom: 16 }}
            items={[
              {
                key: 'verdict',
                label: t('shadow.result.verdict'),
                children: verdict ? (
                  <Tag color={VERDICT_COLORS[verdict]}>{t(`shadow.verdict.${verdict}`)}</Tag>
                ) : (
                  '—'
                ),
              },
              {
                key: 'auto',
                label: t('shadow.result.autoAction'),
                children: renderAction(run.auto_action),
              },
              {
                key: 'human',
                label: t('shadow.result.humanAction'),
                children: run.human_outcome ? (
                  <Space direction="vertical" size={0}>
                    {renderAction(run.human_outcome.action)}
                    {run.human_outcome.note && (
                      <Text type="secondary" style={{ fontSize: 12 }}>
                        {run.human_outcome.note}
                      </Text>
                    )}
                  </Space>
                ) : (
                  <Text type="secondary">{t('shadow.result.noHuman')}</Text>
                ),
              },
            ]}
          />
          {run.tool_intents.length === 0 && run.decisions.length === 0 ? (
            <Empty description={t('shadow.result.empty')} />
          ) : (
            <>
              <Text strong>{t('shadow.result.intentsTitle')}</Text>
              <Table
                size="small"
                rowKey="node_id"
                columns={intentColumns}
                dataSource={run.tool_intents}
                pagination={false}
                style={{ marginBottom: 16 }}
              />
              <Text strong>{t('shadow.result.decisionsTitle')}</Text>
              <Table
                size="small"
                rowKey="node_id"
                columns={decisionColumns}
                dataSource={run.decisions}
                pagination={false}
              />
            </>
          )}
        </>
      )}
    </Modal>
  )
}
