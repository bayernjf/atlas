import { useCallback, useEffect, useState, type ReactElement } from 'react'
import {
  App,
  Button,
  Card,
  Input,
  Layout,
  Popconfirm,
  Space,
  Table,
  Tag,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { UserBadge } from '../components/UserBadge'
import type { Principal } from '../lib/auth'
import {
  createAdapterDraft,
  dryRunAdapterDraft,
  listAdapterDrafts,
  putAdapterDraftDecision,
  type AdapterDraft,
} from '../lib/adapterDrafts'
import { roleCan } from '../lib/auth'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type AdapterDraftsProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

const DECISION_TONE: Record<string, string> = {
  adopted: 'green',
  dismissed: 'default',
}

/**
 * 打包 BQ（docs/133 D71+F-P2）：NL 造工具管理面。
 * operate 可发起草稿与 dry-run；admin 可人工采纳/驳回（auth_type 非 none 须二次确认）。
 * 草稿不自动生效——采纳即人审（F-P2），runtime 只装配 adopted 草稿。
 * 写调用面经 lib/adapterDrafts.ts 转发（页面不直接 import apiClient，U1124 机检）。
 */
export function AdapterDrafts({ principal, onLogout, onBack }: AdapterDraftsProps): ReactElement {
  const { t } = useTranslation('adapterDrafts')
  const { message, modal } = App.useApp()
  const [drafts, setDrafts] = useState<AdapterDraft[]>([])
  const [prompt, setPrompt] = useState('')
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    setDrafts(await listAdapterDrafts())
  }, [])

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    void refresh()
  }, [refresh])
  /* oxlint-enable react/set-state-in-effect */

  const create = async () => {
    if (!prompt.trim()) return
    setBusy(true)
    try {
      await createAdapterDraft(prompt.trim())
      setPrompt('')
      await refresh()
      message.success(t('create.created'))
    } finally {
      setBusy(false)
    }
  }

  const check = async (draft: AdapterDraft) => {
    await dryRunAdapterDraft(draft.draft_id)
    await refresh()
  }

  const decide = async (draft: AdapterDraft, decision: 'adopted' | 'dismissed') => {
    const authConfirmed =
      decision === 'adopted' && draft.definition.auth_type !== 'none'
        ? await new Promise<boolean>((resolve) => {
            modal.confirm({
              title: t('decide.confirmTitle'),
              content: t('decide.confirmBody'),
              okText: t('decide.confirmOk'),
              onOk: () => resolve(true),
              onCancel: () => resolve(false),
            })
          })
        : false
    if (decision === 'adopted' && draft.definition.auth_type !== 'none' && !authConfirmed) return
    await putAdapterDraftDecision(draft.draft_id, decision, authConfirmed)
    await refresh()
  }

  const columns: ColumnsType<AdapterDraft> = [
    { title: t('list.id'), dataIndex: 'draft_id', key: 'draft_id' },
    {
      title: t('list.definition'),
      key: 'definition',
      render: (_, d) => (
        <Space direction="vertical" size={0}>
          <Typography.Text strong>{d.definition.name}</Typography.Text>
          <Typography.Text type="secondary">{d.definition.base_url}</Typography.Text>
          <Typography.Text type="secondary" copyable={{ text: JSON.stringify(d.definition) }}>
            {t('list.capabilities')}: {d.definition.capabilities.map((c) => c.name).join('、')}
          </Typography.Text>
        </Space>
      ),
    },
    {
      title: t('list.permission'),
      key: 'permission',
      render: (_, d) => (
        <Tag color={d.definition.permission === 'read' ? 'blue' : 'orange'}>
          {d.definition.permission}
          {d.definition.auth_type !== 'none' ? ` (${d.definition.auth_type})` : ''}
        </Tag>
      ),
    },
    {
      title: t('list.reachability'),
      key: 'reachability',
      render: (_, d) =>
        d.reachability ? (
          <Tag color={d.reachability.ok ? 'green' : 'red'}>{d.reachability.ok ? 'OK' : 'ERR'}</Tag>
        ) : (
          <Typography.Text type="secondary">{t('list.untested')}</Typography.Text>
        ),
    },
    {
      title: t('list.decision'),
      key: 'decision',
      render: (_, d) =>
        d.decision_status ? (
          <Tag color={DECISION_TONE[d.decision_status] ?? 'default'}>{d.decision_status}</Tag>
        ) : (
          <Typography.Text type="secondary">{t('list.pending')}</Typography.Text>
        ),
    },
    {
      title: t('list.action'),
      key: 'action',
      render: (_, d) => (
        <Space>
          {roleCan(principal.role, 'operate') && (
            <Button size="small" onClick={() => void check(d)}>
              {t('list.dryRun')}
            </Button>
          )}
          {roleCan(principal.role, 'administer') && d.decision_status !== 'adopted' && (
            <Button size="small" type="primary" onClick={() => void decide(d, 'adopted')}>
              {t('list.adopt')}
            </Button>
          )}
          {roleCan(principal.role, 'administer') && d.decision_status !== 'dismissed' && (
            <Popconfirm title={t('list.dismissConfirm')} onConfirm={() => void decide(d, 'dismissed')}>
              <Button size="small" danger>
                {t('list.dismiss')}
              </Button>
            </Popconfirm>
          )}
        </Space>
      ),
    },
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Button onClick={onBack}>{t('common:nav.back')}</Button>
        <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
          {t('page.title')}
        </Typography.Title>
        <span style={{ flex: 1 }} />
        <UserBadge principal={principal} onLogout={onLogout} />
      </Header>
      <Content style={{ padding: 24 }}>
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          {roleCan(principal.role, 'operate') && (
            <Card title={t('create.title')}>
              <Space.Compact style={{ width: '100%' }}>
                <Input
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder={t('create.placeholder')}
                  aria-label={t('create.placeholder')}
                  onPressEnter={() => void create()}
                />
                <Button type="primary" loading={busy} disabled={!prompt.trim()} onClick={() => void create()}>
                  {t('create.submit')}
                </Button>
              </Space.Compact>
            </Card>
          )}
          <Card title={t('list.title')}>
            {drafts.length === 0 ? (
              <Typography.Text type="secondary">{t('list.empty')}</Typography.Text>
            ) : (
              <Table rowKey="draft_id" columns={columns} dataSource={drafts} pagination={false} />
            )}
          </Card>
        </Space>
      </Content>
    </Layout>
  )
}
