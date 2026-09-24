import { useEffect, useState } from 'react'
import { Alert, Button, Modal, Popconfirm, Space, Tag, Typography } from 'antd'
import { useTranslation } from '../../locales'
import {
  getAlertRuleTemplate,
  getAlertRuleTemplates,
  updateRules,
  type AlertRuleTemplateSummary,
} from '../../lib/apiClient'

/**
 * docs/59 F-1：内置只读告警规则模板市场。
 * 列表来自只读端点；「一键应用」取模板完整 config 后复用 PUT /api/monitoring/rules
 * 全量替换（administer），Popconfirm 二次确认；后端不提供模板写端点。
 */
export function AlertRuleTemplateMarket({ onApplied }: { onApplied?: () => void }) {
  const { t } = useTranslation('monitoring')
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<AlertRuleTemplateSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [applyingId, setApplyingId] = useState<string | null>(null)
  const [appliedName, setAppliedName] = useState('')
  const [applyError, setApplyError] = useState('')

  const openMarket = () => {
    setAppliedName('')
    setApplyError('')
    setLoadError('')
    setLoading(true)
    setOpen(true)
  }

  useEffect(() => {
    if (!open) return
    let cancelled = false
    getAlertRuleTemplates()
      .then((data) => {
        if (!cancelled) setItems(data)
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [open])

  const apply = async (id: string, name: string) => {
    setApplyingId(id)
    setApplyError('')
    setAppliedName('')
    try {
      const detail = await getAlertRuleTemplate(id)
      // 复用规则全量替换端点（administer，后端过 validate_rules）。
      await updateRules(detail.config)
      setAppliedName(name)
      onApplied?.()
    } catch (err) {
      setApplyError(err instanceof Error ? err.message : String(err))
    } finally {
      setApplyingId(null)
    }
  }

  return (
    <>
      <Button onClick={openMarket}>{t('templates.button')}</Button>
      <Modal
        title={t('templates.modalTitle')}
        open={open}
        onCancel={() => setOpen(false)}
        footer={null}
        width={640}
      >
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          {t('templates.hint')}
        </Typography.Paragraph>
        {loadError && (
          <Alert type="error" showIcon message={t('templates.loadFailed')} description={loadError} style={{ marginBottom: 12 }} />
        )}
        {appliedName && (
          <Alert type="success" showIcon message={t('templates.applied', { name: appliedName })} style={{ marginBottom: 12 }} />
        )}
        {applyError && (
          <Alert type="error" showIcon message={t('templates.applyFailed')} description={applyError} style={{ marginBottom: 12 }} />
        )}
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          {items.map((tpl) => (
            <div
              key={tpl.id}
              style={{ border: '1px solid var(--ant-color-border, #f0f0f0)', borderRadius: 8, padding: 12 }}
            >
              <Space wrap style={{ justifyContent: 'space-between', width: '100%' }}>
                <Space wrap>
                  <strong>{tpl.name}</strong>
                  {tpl.tags.map((tag) => (
                    <Tag key={tag}>{tag}</Tag>
                  ))}
                </Space>
                <Popconfirm
                  title={t('templates.confirmTitle')}
                  okText={t('templates.confirmOk')}
                  cancelText={t('templates.confirmCancel')}
                  onConfirm={() => apply(tpl.id, tpl.name)}
                  okButtonProps={{ loading: applyingId === tpl.id }}
                >
                  <Button size="small" type="primary" ghost loading={applyingId === tpl.id}>
                    {t('templates.apply')}
                  </Button>
                </Popconfirm>
              </Space>
              <Typography.Paragraph type="secondary" style={{ margin: '8px 0 0' }}>
                {tpl.description}
              </Typography.Paragraph>
            </div>
          ))}
          {!loading && items.length === 0 && !loadError && (
            <Typography.Text type="secondary">{t('templates.empty')}</Typography.Text>
          )}
        </Space>
      </Modal>
    </>
  )
}
