import { useEffect, useState } from 'react'
import { Alert, Button, Form, Input, Modal, Popconfirm, Space, Tag, Typography } from 'antd'
import { useTranslation } from '../../locales'
import {
  createAlertRuleTemplate,
  deleteAlertRuleTemplate,
  getAlertRuleTemplate,
  getAlertRuleTemplates,
  updateAlertRuleTemplate,
  updateRules,
  type AlertRuleTemplateSummary,
  type RuleConfig,
} from '../../lib/apiClient'
import { RuleConfigEditor } from './RuleConfigEditor'
import { defaultRuleConfig, validateRuleConfig } from '../../lib/ruleConfig'

/**
 * docs/59 F-1 + 打包 ZS（docs/102）：告警规则模板市场。
 * 列表＝内置只读目录＋用户自建合并（source 区分）；「一键应用」取模板完整 config
 * 复用 PUT /api/monitoring/rules 全量替换（administer），两类模板同样适用；
 * admin 可新建/编辑/删除用户模板（config 走共享 RuleConfigEditor 可视化表单，
 * 提交前 validateRuleConfig 拦截；docs/103 打包 ZT 替换原裸 JSON 文本框）。
 */
export function AlertRuleTemplateMarket({
  canAdmin,
  onApplied,
}: {
  canAdmin?: boolean
  onApplied?: () => void
}) {
  const { t } = useTranslation('monitoring')
  const [open, setOpen] = useState(false)
  const [items, setItems] = useState<AlertRuleTemplateSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [applyingId, setApplyingId] = useState<string | null>(null)
  const [appliedName, setAppliedName] = useState('')
  const [applyError, setApplyError] = useState('')

  const [formOpen, setFormOpen] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [formSaving, setFormSaving] = useState(false)
  const [formError, setFormError] = useState('')
  const [formSuccess, setFormSuccess] = useState('')
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState('')
  const [form] = Form.useForm()
  // docs/103：config 不进 antd Form，由共享编辑器纯受控管理
  const [config, setConfig] = useState<RuleConfig | null>(null)

  const refresh = async (silent = false) => {
    if (!silent) setLoading(true)
    setLoadError('')
    try {
      setItems(await getAlertRuleTemplates())
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }

  const openMarket = () => {
    setAppliedName('')
    setApplyError('')
    setDeleteError('')
    setLoading(true)
    setOpen(true)
    void refresh(true)
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

  const openCreate = () => {
    setEditingId(null)
    setFormError('')
    setFormSuccess('')
    form.resetFields()
    setConfig(defaultRuleConfig())
    setFormOpen(true)
  }

  const openEdit = async (id: string) => {
    setEditingId(id)
    setFormError('')
    setFormSuccess('')
    try {
      const detail = await getAlertRuleTemplate(id)
      form.setFieldsValue({
        name: detail.name,
        description: detail.description,
        tags: detail.tags.join(', '),
      })
      // 结构拷贝，避免编辑态污染列表/详情对象
      setConfig(structuredClone(detail.config))
      setFormOpen(true)
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err))
    }
  }

  const submit = async () => {
    const values = form.getFieldsValue()
    const name = String(values.name ?? '').trim()
    if (!name) {
      setFormError(t('templates.nameRequired'))
      return
    }
    const ruleConfig = config
    if (!ruleConfig || validateRuleConfig(ruleConfig).length > 0) {
      setFormError(t('templates.configInvalid'))
      return
    }
    const tags = String(values.tags ?? '')
      .split(/[,，]/)
      .map((s) => s.trim())
      .filter(Boolean)
    setFormSaving(true)
    setFormError('')
    setFormSuccess('')
    try {
      const payload = { name, description: String(values.description ?? '').trim(), tags, config: ruleConfig }
      if (editingId) {
        await updateAlertRuleTemplate(editingId, payload)
        setFormSuccess(t('templates.updateSuccess'))
      } else {
        await createAlertRuleTemplate(payload)
        setFormSuccess(t('templates.createSuccess'))
      }
      await refresh(true)
      onApplied?.()
    } catch (err) {
      setFormError(err instanceof Error ? err.message : String(err))
    } finally {
      setFormSaving(false)
    }
  }

  const remove = async (id: string) => {
    setDeletingId(id)
    setDeleteError('')
    try {
      await deleteAlertRuleTemplate(id)
      await refresh(true)
    } catch (err) {
      setDeleteError(err instanceof Error ? err.message : String(err))
    } finally {
      setDeletingId(null)
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
        width={680}
      >
        <Typography.Paragraph type="secondary" style={{ marginTop: 0 }}>
          {t('templates.hint')}
        </Typography.Paragraph>
        {canAdmin && (
          <Button type="dashed" style={{ marginBottom: 12 }} onClick={openCreate}>
            {t('templates.new')}
          </Button>
        )}
        {loadError && (
          <Alert type="error" showIcon message={t('templates.loadFailed')} description={loadError} style={{ marginBottom: 12 }} />
        )}
        {appliedName && (
          <Alert type="success" showIcon message={t('templates.applied', { name: appliedName })} style={{ marginBottom: 12 }} />
        )}
        {applyError && (
          <Alert type="error" showIcon message={t('templates.applyFailed')} description={applyError} style={{ marginBottom: 12 }} />
        )}
        {deleteError && (
          <Alert type="error" showIcon message={t('templates.deleteFailed')} description={deleteError} style={{ marginBottom: 12 }} />
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
                  <Tag color={tpl.source === 'user' ? 'blue' : 'default'}>
                    {tpl.source === 'user' ? t('templates.sourceUser') : t('templates.sourceBuiltin')}
                  </Tag>
                  {tpl.tags.map((tag) => (
                    <Tag key={tag}>{tag}</Tag>
                  ))}
                </Space>
                <Space wrap>
                  {canAdmin && tpl.source === 'user' && (
                    <>
                      <Button size="small" onClick={() => openEdit(tpl.id)}>
                        {t('templates.edit')}
                      </Button>
                      <Popconfirm
                        title={t('templates.deleteConfirmTitle')}
                        okText={t('templates.confirmOk')}
                        cancelText={t('templates.confirmCancel')}
                        onConfirm={() => remove(tpl.id)}
                        okButtonProps={{ loading: deletingId === tpl.id }}
                      >
                        <Button size="small" danger loading={deletingId === tpl.id}>
                          {t('templates.delete')}
                        </Button>
                      </Popconfirm>
                    </>
                  )}
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
      <Modal
        title={editingId ? t('templates.edit') : t('templates.new')}
        open={formOpen}
        onCancel={() => setFormOpen(false)}
        onOk={submit}
        okText={t('templates.save')}
        cancelText={t('templates.cancel')}
        confirmLoading={formSaving}
        width={760}
      >
        {formError && (
          <Alert type="error" showIcon message={formError} style={{ marginBottom: 12 }} />
        )}
        {formSuccess && (
          <Alert type="success" showIcon message={formSuccess} style={{ marginBottom: 12 }} />
        )}
        <Form form={form} layout="vertical" requiredMark={false}>
          <Form.Item label={t('templates.nameLabel')} name="name" rules={[{ required: true }]}>
            <Input maxLength={64} />
          </Form.Item>
          <Form.Item label={t('templates.descLabel')} name="description">
            <Input.TextArea maxLength={500} autoSize={{ minRows: 1, maxRows: 3 }} />
          </Form.Item>
          <Form.Item label={t('templates.tagsLabel')} name="tags">
            <Input />
          </Form.Item>
          <Form.Item label={t('templates.configLabel')} required>
            {config && (
              <div style={{ maxHeight: 420, overflowY: 'auto', paddingRight: 8 }}>
                <RuleConfigEditor value={config} onChange={setConfig} />
              </div>
            )}
          </Form.Item>
        </Form>
      </Modal>
    </>
  )
}

