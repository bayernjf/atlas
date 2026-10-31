import { useEffect, useMemo, useState } from 'react'
import { App, Alert, Button, Input, Modal, Select, Space, Switch, Tag, Typography } from 'antd'
import {
  ApiError,
  getInboundMappings,
  listAgents,
  listGraphs,
  putInboundMappings,
  type AgentView,
  type SavedGraphSummary,
} from '../lib/apiClient'
import { useTranslation } from '../locales'
import { InboundSessionModal } from './InboundSessionModal'

const MAX_MAPPINGS = 10

type Draft = {
  conversationId: string
  graphId: string
  agentId: string | null
  enabled: boolean
}

type Props = {
  bindingId: string | null
  onClose: () => void
}

// 打包 BO（docs/131 D-D3）：入站会话映射配置（挂 binding，与 WebhookSubscriptionsModal 同范式）。
export function InboundMappingsModal({ bindingId, onClose }: Props) {
  const { t } = useTranslation('channels')
  const { message } = App.useApp()
  const [drafts, setDrafts] = useState<Draft[] | null>(null)
  const [graphs, setGraphs] = useState<SavedGraphSummary[]>([])
  const [agents, setAgents] = useState<AgentView[]>([])
  const [saving, setSaving] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)

  useEffect(() => {
    if (!bindingId) return
    void listGraphs()
      .then(setGraphs)
      .catch(() => setGraphs([]))
    void listAgents()
      .then(setAgents)
      .catch(() => setAgents([]))
    getInboundMappings(bindingId)
      .then((mappings) =>
        setDrafts(
          mappings.map((m) => ({
            conversationId: m.conversationId ?? '',
            graphId: m.graphId ?? '',
            agentId: m.agentId ?? null,
            enabled: m.enabled,
          })),
        ),
      )
      .catch((err: unknown) => {
        message.error(err instanceof ApiError ? err.message : t('inbound.error.load'))
        onClose()
      })
  }, [bindingId, message, onClose, t])

  const graphOptions = useMemo(
    () => graphs.map((graph) => ({ value: graph.id, label: graph.id })),
    [graphs],
  )

  const agentOptions = useMemo(
    () =>
      agents
        .filter((agent) => agent.enabled)
        .map((agent) => ({ value: agent.id, label: agent.name })),
    [agents],
  )

  const patch = (index: number, partial: Partial<Draft>) =>
    setDrafts(
      (previous) =>
        previous?.map((draft, i) => (i === index ? { ...draft, ...partial } : draft)) ?? null,
    )

  const save = async () => {
    if (!bindingId || !drafts) return
    setSaving(true)
    try {
      await putInboundMappings(
        bindingId,
        drafts.map((draft) => ({
          conversationId: draft.conversationId.trim(),
          graphId: draft.graphId,
          agentId: draft.agentId,
          enabled: draft.enabled,
        })),
      )
      message.success(t('inbound.message.saved'))
      onClose()
    } catch (err) {
      // 后端 422 数组 detail 已在 requestError 解析成 CompileValidationError.message（单串）。
      message.error(err instanceof ApiError ? err.message : t('inbound.error.save'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      title={t('inbound.modal.title')}
      open={bindingId !== null}
      onCancel={onClose}
      onOk={save}
      okText={t('common:button.save')}
      cancelText={t('common:button.cancel')}
      confirmLoading={saving}
      width={880}
      destroyOnHidden
    >
      <Space direction="vertical" size={12} style={{ width: '100%' }}>
        <Alert type="info" showIcon message={t('inbound.hint')} />
        <Typography.Title level={5} style={{ margin: 0 }}>
          {t('inbound.subsTitle')}
        </Typography.Title>
        {(drafts ?? []).map((draft, index) => (
          <Space key={index} size={8} align="center" wrap>
            <Input
              value={draft.conversationId}
              onChange={(event) => patch(index, { conversationId: event.target.value })}
              placeholder={t('inbound.conversationPlaceholder')}
              aria-label={`${t('inbound.subsTitle')} #${index + 1} · ${t('inbound.conversationPlaceholder')}`}
              style={{ width: 220 }}
            />
            <Select
              value={draft.graphId || undefined}
              onChange={(value) => patch(index, { graphId: value })}
              placeholder={t('inbound.graphPlaceholder')}
              aria-label={`${t('inbound.subsTitle')} #${index + 1} · ${t('inbound.graphPlaceholder')}`}
              options={graphOptions}
              style={{ width: 200 }}
              showSearch
            />
            <Select
              value={draft.agentId ?? undefined}
              onChange={(value) => patch(index, { agentId: value ?? null })}
              placeholder={t('inbound.agentPlaceholder')}
              aria-label={`${t('inbound.subsTitle')} #${index + 1} · ${t('inbound.agentPlaceholder')}`}
              options={agentOptions}
              style={{ width: 160 }}
              allowClear
              showSearch
            />
            <Switch
              checked={draft.enabled}
              aria-label={`${t('inbound.subsTitle')} #${index + 1} · ${t('inbound.enabled')}`}
              onChange={(checked) => patch(index, { enabled: checked })}
            />
            <Tag color={draft.enabled ? 'green' : 'default'}>
              {draft.enabled ? t('inbound.enabled') : t('inbound.disabled')}
            </Tag>
            <Button
              size="small"
              disabled={!draft.conversationId.trim()}
              onClick={() => setSessionId(draft.conversationId.trim())}
            >
              {t('inbound.session')}
            </Button>
            <Button
              size="small"
              danger
              onClick={() =>
                setDrafts((previous) => previous?.filter((_, i) => i !== index) ?? null)
              }
            >
              {t('common:button.delete')}
            </Button>
          </Space>
        ))}
        <Button
          type="dashed"
          disabled={(drafts ?? []).length >= MAX_MAPPINGS}
          onClick={() =>
            setDrafts((previous) => [
              ...(previous ?? []),
              { conversationId: '', graphId: '', agentId: null, enabled: true },
            ])
          }
        >
          {t('inbound.add')}
        </Button>
      </Space>
      <InboundSessionModal
        key={sessionId ?? 'closed'}
        conversationId={sessionId}
        onClose={() => setSessionId(null)}
      />
    </Modal>
  )
}
