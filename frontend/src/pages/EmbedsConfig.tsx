import { useCallback, useEffect, useState, type ReactElement } from 'react'
import {
  Alert,
  App,
  Button,
  Card,
  InputNumber,
  Layout,
  Select,
  Space,
  Switch,
  Table,
  Typography,
} from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { UserBadge } from '../components/UserBadge'
import type { Principal } from '../lib/auth'
import {
  createEmbedToken,
  listEmbedConfigs,
  listGraphs,
  putEmbedConfig,
  type EmbedConfig,
  type EmbedTokenResponse,
  type SavedGraphSummary,
} from '../lib/embeds'
import { roleCan } from '../lib/auth'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type EmbedsConfigProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

/**
 * 打包 BQ（docs/133 D76）：网页嵌入配置管理面。
 * administer 页：按图开关只读嵌入（enabled 缺省 false＝fail-closed）、配 TTL（1–86400），
 * 签发短链后可直接外嵌；只读面本身在 Embed.tsx（无登录独立外壳）。
 * 写调用面经 lib/embeds.ts 转发（页面不直接 import apiClient，U1124 机检）。
 */
export function EmbedsConfig({ principal, onLogout, onBack }: EmbedsConfigProps): ReactElement {
  const { t } = useTranslation('embeds')
  const { message, modal } = App.useApp()
  const [configs, setConfigs] = useState<EmbedConfig[]>([])
  const [graphs, setGraphs] = useState<SavedGraphSummary[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const [ttl, setTtl] = useState(3600)
  const [busy, setBusy] = useState(false)

  const refresh = useCallback(async () => {
    const [cfgs, gs] = await Promise.all([listEmbedConfigs(), listGraphs()])
    setConfigs(cfgs)
    setGraphs(gs)
  }, [])

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    void refresh()
  }, [refresh])
  /* oxlint-enable react/set-state-in-effect */

  const save = async () => {
    if (!selected) return
    setBusy(true)
    try {
      await putEmbedConfig({
        graph_id: selected,
        enabled: true,
        token_ttl_seconds: ttl,
      })
      await refresh()
      message.success(t('config.saved'))
    } finally {
      setBusy(false)
    }
  }

  const toggle = async (cfg: EmbedConfig, enabled: boolean) => {
    await putEmbedConfig({
      graph_id: cfg.graph_id,
      enabled,
      token_ttl_seconds: cfg.token_ttl_seconds,
    })
    await refresh()
  }

  const issueToken = async (cfg: EmbedConfig) => {
    const resp: EmbedTokenResponse = await createEmbedToken(cfg.graph_id)
    void navigator.clipboard?.writeText(resp.tokenUrl)
    modal.info({ content: resp.tokenUrl })
  }

  const columns: ColumnsType<EmbedConfig> = [
    { title: t('config.graph'), dataIndex: 'graph_id', key: 'graph_id' },
    {
      title: t('config.enabled'),
      key: 'enabled',
      render: (_, cfg) => (
        <Switch
          checked={cfg.enabled}
          onChange={(v) => void toggle(cfg, v)}
          disabled={!roleCan(principal.role, 'administer')}
        />
      ),
    },
    { title: t('config.ttl'), dataIndex: 'token_ttl_seconds', key: 'ttl' },
    {
      title: t('config.action'),
      key: 'action',
      render: (_, cfg) => (
        <Button
          size="small"
          disabled={!cfg.enabled || !roleCan(principal.role, 'administer')}
          onClick={() => void issueToken(cfg)}
        >
          {t('config.issue')}
        </Button>
      ),
    },
  ]

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <Button onClick={onBack}>{t('common:nav.back')}</Button>
        <Typography.Title level={4} style={{ color: '#fff', margin: 0 }}>
          {t('config.title')}
        </Typography.Title>
        <span style={{ flex: 1 }} />
        <UserBadge principal={principal} onLogout={onLogout} />
      </Header>
      <Content style={{ padding: 24 }}>
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <Alert type="info" showIcon message={t('config.hint')} />
          <Card title={t('config.new')}>
            <Space wrap>
              <Select
                style={{ minWidth: 260 }}
                placeholder={t('config.selectGraph')}
                value={selected ?? undefined}
                onChange={setSelected}
                options={graphs.map((g) => ({ value: g.id, label: g.id }))}
              />
              <InputNumber
                min={1}
                max={86400}
                value={ttl}
                onChange={(v) => setTtl(v ?? 3600)}
                addonAfter={t('config.ttlUnit')}
              />
              <Button type="primary" loading={busy} disabled={!selected} onClick={() => void save()}>
                {t('config.enable')}
              </Button>
            </Space>
          </Card>
          <Card title={t('config.list')}>
            {configs.length === 0 ? (
              <Typography.Text type="secondary">{t('config.empty')}</Typography.Text>
            ) : (
              <Table rowKey="graph_id" columns={columns} dataSource={configs} pagination={false} />
            )}
          </Card>
        </Space>
      </Content>
    </Layout>
  )
}
