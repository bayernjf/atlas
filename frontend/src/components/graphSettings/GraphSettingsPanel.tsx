import { Divider, Input, InputNumber, Select, Space, Typography } from 'antd'
import { useEditorStore } from '../../store/editorStore'
import { useTranslation } from '../../locales'

/**
 * 打包 AL（docs/123 §2.1/§2.2）：图级全局异常降级 + selfHealing 自愈配置面板。
 * 存于 editorStore.graphSettings（camelCase 编辑器形态），serializeGraph 时
 * 转 snake_case 顶层字段透传；无子图可选时提示先保存子图。
 * 三种降级策略：mark_only（标记后失败）/ route_to_subgraph（诊断子图裁决）/
 * escalate_to_human（P-1=C：审批/通知闭环）。
 */
export function GraphSettingsPanel() {
  const { t } = useTranslation('editor')
  const graphSettings = useEditorStore((state) => state.graphSettings)
  const updateGraphSettings = useEditorStore((state) => state.updateGraphSettings)
  const nodes = useEditorStore((state) => state.nodes)

  const gh = graphSettings.globalErrorHandler ?? {}
  const sh = graphSettings.selfHealing ?? {}

  // 可作诊断/路由子图的候选（subgraph 节点），同时允许手填已保存图引用
  const subgraphOptions = nodes
    .filter((node) => node.data.kind === 'subgraph')
    .map((node) => ({ value: node.id, label: `${node.data.label}（${node.id}）` }))

  const patchGh = (patch: Partial<NonNullable<typeof graphSettings.globalErrorHandler>>) =>
    updateGraphSettings({ globalErrorHandler: { ...gh, ...patch } })
  const patchSh = (patch: Partial<NonNullable<typeof graphSettings.selfHealing>>) =>
    updateGraphSettings({ selfHealing: { ...sh, ...patch } })

  return (
    <div className="graph-settings-panel">
      <Typography.Text strong>{t('graphSettings.globalErrorTitle')}</Typography.Text>
      <Space direction="vertical" style={{ width: '100%', marginTop: 8 }} size={8}>
        <Select
          allowClear
          placeholder={t('graphSettings.strategyPlaceholder')}
          style={{ width: '100%' }}
          value={gh.onUncaughtError}
          onChange={(v) =>
            patchGh({
              onUncaughtError: v,
              // 切换策略时清空仅在原策略下有效的字段
              errorSubgraphId: v === 'route_to_subgraph' ? gh.errorSubgraphId : undefined,
              escalation: v === 'escalate_to_human' ? gh.escalation : undefined,
            })
          }
          options={[
            { value: 'mark_only', label: t('graphSettings.strategyMark') },
            { value: 'route_to_subgraph', label: t('graphSettings.strategyRoute') },
            { value: 'escalate_to_human', label: t('graphSettings.strategyEscalate') },
          ]}
        />
        {gh.onUncaughtError === 'route_to_subgraph' && (
          <Select
            showSearch
            placeholder={t('graphSettings.errorSubgraphPlaceholder')}
            style={{ width: '100%' }}
            value={gh.errorSubgraphId}
            onChange={(v) => patchGh({ errorSubgraphId: v })}
            options={subgraphOptions}
            notFoundContent={t('graphSettings.noSubgraph')}
            allowClear
          />
        )}
        {gh.onUncaughtError === 'escalate_to_human' && (
          <>
            <Input
              placeholder={t('graphSettings.escalationChannel')}
              value={gh.escalation?.channel}
              onChange={(e) => patchGh({ escalation: { ...gh.escalation, channel: e.target.value } })}
            />
            <Input
              placeholder={t('graphSettings.escalationRecipients')}
              value={gh.escalation?.recipients?.join(', ')}
              onChange={(e) =>
                patchGh({
                  escalation: {
                    ...gh.escalation,
                    recipients: e.target.value
                      .split(',')
                      .map((s) => s.trim())
                      .filter(Boolean),
                  },
                })
              }
            />
          </>
        )}
      </Space>

      <Divider style={{ margin: '12px 0' }} />

      <Typography.Text strong>{t('graphSettings.selfHealingTitle')}</Typography.Text>
      <Space direction="vertical" style={{ width: '100%', marginTop: 8 }} size={8}>
        <Select
          mode="tags"
          placeholder={t('graphSettings.triggerOnPlaceholder')}
          style={{ width: '100%' }}
          value={sh.triggerOn}
          onChange={(v) => patchSh({ triggerOn: v })}
          tokenSeparators={[',']}
          options={[
            { value: 'NODE_EXECUTION_FAILED', label: 'NODE_EXECUTION_FAILED' },
            { value: 'TOOL_EXECUTION_FAILED', label: 'TOOL_EXECUTION_FAILED' },
          ]}
        />
        <Select
          showSearch
          placeholder={t('graphSettings.diagnoseSubgraphPlaceholder')}
          style={{ width: '100%' }}
          value={sh.diagnoseSubgraphId}
          onChange={(v) => patchSh({ diagnoseSubgraphId: v })}
          options={subgraphOptions}
          notFoundContent={t('graphSettings.noSubgraph')}
          allowClear
        />
        <Space>
          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
            {t('graphSettings.maxAttempts')}
          </Typography.Text>
          <InputNumber
            min={1}
            max={3}
            value={sh.maxAttempts ?? 1}
            onChange={(v) => patchSh({ maxAttempts: v ?? 1 })}
            style={{ width: 90 }}
          />
        </Space>
        <Input
          placeholder={t('graphSettings.continueWhenPlaceholder')}
          value={sh.continueWhen}
          onChange={(e) => patchSh({ continueWhen: e.target.value })}
        />
      </Space>

      {!gh.onUncaughtError && !sh.diagnoseSubgraphId && (
        <Typography.Text type="secondary" style={{ fontSize: 12, display: 'block', marginTop: 8 }}>
          {t('graphSettings.emptyHint')}
        </Typography.Text>
      )}
    </div>
  )
}
