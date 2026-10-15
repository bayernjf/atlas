import { useCallback, useEffect, useState, type ReactElement } from 'react'
import {
  Alert,
  Card,
  Descriptions,
  Layout,
  List,
  Space,
  Statistic,
  Tag,
  Typography,
} from 'antd'
import { getEmbedReadonlyView, type EmbedReadonlyView } from '../lib/embeds'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

/**
 * 打包 BQ（docs/133 D76）：网页嵌入只读面。
 * 无登录态独立外壳（同 EmailApproval 纪律）：token 从 URL /embed/{token} 来，
 * 验签失败后端统一 404，前端只呈现"链接无效"——不猜图、不透露任何图内细节。
 */
export function Embed({ token }: { token: string }): ReactElement {
  const { t } = useTranslation('embeds')
  const [view, setView] = useState<EmbedReadonlyView | null>(null)
  const [invalid, setInvalid] = useState(false)

  const refresh = useCallback(async () => {
    try {
      setView(await getEmbedReadonlyView(token))
      setInvalid(false)
    } catch {
      setView(null)
      setInvalid(true)
    }
  }, [token])

  /* oxlint-disable react/set-state-in-effect */
  useEffect(() => {
    void refresh()
  }, [refresh])
  /* oxlint-enable react/set-state-in-effect */

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Header style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <Typography.Title level={3} style={{ color: '#fff', margin: 0 }}>
          {t('readonly.title')}
        </Typography.Title>
      </Header>
      <Content style={{ padding: 24, maxWidth: 720, width: '100%', margin: '0 auto' }}>
        {invalid ? (
          <Alert type="error" showIcon message={t('readonly.invalid')} />
        ) : !view ? (
          <Alert type="info" showIcon message={t('readonly.loading')} />
        ) : (
          <Space direction="vertical" size="large" style={{ width: '100%' }}>
            <Card>
              <Descriptions column={1} size="small" bordered>
                <Descriptions.Item label={t('readonly.graph')}>{view.graphId}</Descriptions.Item>
                <Descriptions.Item label={t('readonly.name')}>{view.name}</Descriptions.Item>
              </Descriptions>
              <Space size="large" style={{ marginTop: 16 }}>
                <Statistic title={t('readonly.nodeCount')} value={view.nodeCount} />
                <Statistic
                  title={t('readonly.lastRun')}
                  valueRender={() =>
                    view.lastRun ? (
                      <Space>
                        <Tag color={view.lastRun.status === 'completed' ? 'green' : 'orange'}>
                          {view.lastRun.status}
                        </Tag>
                        <Typography.Text type="secondary">{view.lastRun.runId}</Typography.Text>
                      </Space>
                    ) : (
                      <Typography.Text type="secondary">{t('readonly.noRun')}</Typography.Text>
                    )
                  }
                />
              </Space>
            </Card>
            <Card title={t('readonly.variables')} size="small">
              {view.variables.length === 0 ? (
                <Typography.Text type="secondary">{t('readonly.noVariables')}</Typography.Text>
              ) : (
                <List
                  size="small"
                  dataSource={view.variables}
                  renderItem={(v) => (
                    <List.Item>
                      <Typography.Text code>{v.name}</Typography.Text>
                      <Typography.Text type="secondary" style={{ wordBreak: 'break-all' }}>
                        {JSON.stringify(v.value)}
                      </Typography.Text>
                    </List.Item>
                  )}
                />
              )}
            </Card>
          </Space>
        )}
      </Content>
    </Layout>
  )
}
