import { useCallback, useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Col,
  Form,
  Input,
  Layout,
  Row,
  Space,
  Switch,
  Tag,
  Typography,
  message,
} from 'antd'
import { roleCan, type Principal } from '../lib/auth'
import { UserBadge } from '../components/UserBadge'
import {
  getModels,
  putBuiltinModel,
  putByokModel,
  type ModelConfigView,
  type ModelOverview,
} from '../lib/apiClient'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type ModelsProps = {
  principal: Principal
  onLogout: () => void
  onBack: () => void
}

const SOURCE_TAGS: Record<string, string> = {
  builtin: 'blue',
  byok: 'green',
  env: 'orange',
  none: 'default',
}

function MaskedKey({ apiKey }: { apiKey: string }) {
  if (!apiKey) return <Typography.Text type="secondary">—</Typography.Text>
  return <Typography.Text code>{apiKey}</Typography.Text>
}

function ModelConfigEditor({
  kind,
  config,
  canEdit,
  onSaved,
  t,
}: {
  kind: 'builtin' | 'byok'
  config: ModelConfigView | null
  canEdit: boolean
  onSaved: () => void
  t: (key: string, options?: Record<string, unknown>) => string
}) {
  const [form] = Form.useForm<{ model: string; apiKey?: string; baseUrl?: string; enabled: boolean }>()
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (config) {
      form.setFieldsValue({
        model: config.model,
        apiKey: '',
        baseUrl: config.baseUrl ?? '',
        enabled: config.enabled,
      })
    } else {
      form.resetFields()
      form.setFieldsValue({ enabled: true })
    }
  }, [config, form])

  const submit = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      const payload = {
        model: values.model,
        ...(values.apiKey ? { apiKey: values.apiKey } : {}),
        ...(values.baseUrl ? { baseUrl: values.baseUrl } : {}),
        enabled: values.enabled,
      }
      if (kind === 'builtin') {
        await putBuiltinModel(payload)
        message.success(t('message.builtinSaved'))
      } else {
        await putByokModel(payload)
        message.success(t('message.byokSaved'))
      }
      onSaved()
    } catch {
      message.error(t('error.save'))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Form
      form={form}
      layout="vertical"
      disabled={!canEdit}
      onFinish={submit}
      initialValues={{ enabled: true }}
    >
      <Form.Item
        name="model"
        label={t('form.model')}
        rules={[{ required: true, message: t('error.modelRequired') }]}
      >
        <Input placeholder={t('form.modelPlaceholder')} />
      </Form.Item>
      <Form.Item
        name="apiKey"
        label={t('form.apiKey')}
        extra={
          config?.apiKey
            ? t('form.apiKeyKeepPlaceholder', { masked: config.apiKey })
            : t('form.apiKeyHint')
        }
      >
        <Input.Password placeholder={t('form.apiKeyPlaceholder')} autoComplete="new-password" />
      </Form.Item>
      <Form.Item name="baseUrl" label={t('form.baseUrl')}>
        <Input placeholder={t('form.baseUrlPlaceholder')} />
      </Form.Item>
      <Form.Item name="enabled" label={t('form.enabled')} valuePropName="checked">
        <Switch />
      </Form.Item>
      {canEdit && (
        <Button type="primary" htmlType="submit" loading={saving}>
          {t('button.save')}
        </Button>
      )}
    </Form>
  )
}

export function Models({ principal, onLogout, onBack }: ModelsProps) {
  const { t } = useTranslation('models')
  const canAdmin = roleCan(principal.role, 'administer')
  const canOperate = roleCan(principal.role, 'operate')

  const [overview, setOverview] = useState<ModelOverview | null>(null)
  const [loading, setLoading] = useState(true)
  const [listError, setListError] = useState('')

  const refresh = useCallback(async () => {
    try {
      const data = await getModels()
      setOverview(data)
      setListError('')
    } catch (error) {
      setListError(error instanceof Error ? error.message : t('error.load'))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => {
    // eslint-disable-next-line react/set-state-in-effect -- 首帧拉取外部 API，setState 均在 await 之后
    void refresh()
  }, [refresh])

  const effective = overview?.effective
  const sourceLabel =
    effective?.source === 'builtin'
      ? t('effective.sourceBuiltin')
      : effective?.source === 'byok'
        ? t('effective.sourceByok')
        : effective?.source === 'env'
          ? t('effective.sourceEnv')
          : t('effective.sourceNone')

  return (
    <Layout className="page-layout">
      <Header className="page-header" style={{ justifyContent: 'space-between' }}>
        <Typography.Title level={3}>{t('title')}</Typography.Title>
        <Space>
          <Button onClick={onBack}>{t('header.back')}</Button>
          <Button onClick={() => void refresh()} loading={loading}>
            {t('header.refresh')}
          </Button>
          <UserBadge principal={principal} onLogout={onLogout} />
        </Space>
      </Header>
      <Content className="page-content">
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          <Card>
            <Typography.Title level={4}>{t('notice.title')}</Typography.Title>
            <Typography.Paragraph>{t('notice.description')}</Typography.Paragraph>
          </Card>

          {listError && <Alert type="error" message={listError} showIcon />}

          <Card
            loading={loading}
            title={
              <Space>
                <span>{t('effective.title')}</span>
                {effective && <Tag color={SOURCE_TAGS[effective.source]}>{sourceLabel}</Tag>}
              </Space>
            }
          >
            {effective ? (
              <Row gutter={[24, 12]}>
                <Col span={8}>
                  <Typography.Text type="secondary">{t('effective.model')}</Typography.Text>
                  <div>
                    <Typography.Text strong>{effective.model || '—'}</Typography.Text>
                  </div>
                </Col>
                <Col span={8}>
                  <Typography.Text type="secondary">API Key</Typography.Text>
                  <div>
                    <MaskedKey apiKey={effective.apiKey} />
                  </div>
                </Col>
                <Col span={8}>
                  <Typography.Text type="secondary">{t('effective.enabled')}</Typography.Text>
                  <div>
                    <Tag color={effective.enabled ? 'green' : 'red'}>
                      {effective.enabled ? t('status.enabled') : t('status.disabled')}
                    </Tag>
                  </div>
                </Col>
              </Row>
            ) : (
              <Typography.Text type="secondary">{t('effective.notConfigured')}</Typography.Text>
            )}
          </Card>

          <Row gutter={24}>
            <Col span={12}>
              <Card
                loading={loading}
                title={t('builtin.title')}
                extra={
                  canAdmin ? (
                    <Tag color="blue">{t('status.enabled')}</Tag>
                  ) : (
                    <Tag>{t('builtin.adminOnly')}</Tag>
                  )
                }
              >
                <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
                  {t('builtin.description')}
                </Typography.Paragraph>
                {!overview?.builtin && !loading && (
                  <Alert
                    type="info"
                    showIcon
                    message={t('builtin.notConfigured')}
                    style={{ marginBottom: 16 }}
                  />
                )}
                <ModelConfigEditor
                  kind="builtin"
                  config={overview?.builtin ?? null}
                  canEdit={canAdmin}
                  onSaved={refresh}
                  t={t}
                />
              </Card>
            </Col>
            <Col span={12}>
              <Card
                loading={loading}
                title={t('byok.title')}
                extra={
                  canOperate ? (
                    <Tag color="green">{t('status.enabled')}</Tag>
                  ) : undefined
                }
              >
                <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
                  {t('byok.description')}
                </Typography.Paragraph>
                {!overview?.byok && !loading && (
                  <Alert
                    type="info"
                    showIcon
                    message={t('byok.notConfigured')}
                    style={{ marginBottom: 16 }}
                  />
                )}
                <ModelConfigEditor
                  kind="byok"
                  config={overview?.byok ?? null}
                  canEdit={canOperate}
                  onSaved={refresh}
                  t={t}
                />
              </Card>
            </Col>
          </Row>
        </Space>
      </Content>
    </Layout>
  )
}
