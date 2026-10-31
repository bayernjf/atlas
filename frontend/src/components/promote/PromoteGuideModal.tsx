/**
 * 试教成图与操作指引（docs/128，打包 AT）。
 *
 * 一个用例两个出口：把录出来的流程升格成可编辑草稿（D68），或导出一份给人看的
 * 操作指引（D72）。两件事共用同一个只读 plan，所以打开就能看到"机器打算改哪几处、
 * 哪里它不敢决定"，而不是点一下保存才发现图被改了。
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { useTranslation } from '../../locales'
import {
  Alert,
  Button,
  Checkbox,
  Modal,
  Space,
  Spin,
  Switch,
  Tabs,
  Tag,
  Typography,
} from 'antd'
import {
  getRecordingGuide,
  planRecordingPromotion,
  promoteRecording,
  saveGraph,
  type GuideDoc,
  type PromotePlan,
} from '../../lib/apiClient'
import {
  countPlan,
  groupByNode,
  guideFilename,
  pruneWarningCounts,
  selectionIsValid,
  toggleSelection,
} from '../../lib/promote'

export type PromoteGuideModalProps = {
  open: boolean
  caseId: string | null
  caseName: string
  onClose: () => void
  /** 草稿存成新流程后回调，拿到新图 id（Editor 用它刷新图清单） */
  onSaved?: (graphId: string) => void
}

export function PromoteGuideModal({
  open,
  caseId,
  caseName,
  onClose,
  onSaved,
}: PromoteGuideModalProps) {
  const { t, i18n } = useTranslation('editor')
  const [plan, setPlan] = useState<PromotePlan | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [prune, setPrune] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [guide, setGuide] = useState<GuideDoc | null>(null)
  const [guideBusy, setGuideBusy] = useState(false)

  const lang = i18n.language?.startsWith('en') ? 'en' : 'zh'

  // 拉候选表。effect 体里不做任何同步 setState（oxlint 0/0 的基线，也是 React Compiler
  // 的要求）：弹窗按 caseId 重新挂载（Editor 侧 key），所以初值就是干净的，
  // "加载中"用 plan===null 派生而不是另一个 state。
  useEffect(() => {
    if (!open || !caseId) return
    let active = true
    planRecordingPromotion(caseId)
      .then((next) => {
        if (active) setPlan(next)
      })
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : String(cause))
      })
    return () => {
      active = false
    }
  }, [open, caseId])

  const planLoading = open && plan === null && error === null

  const nodeTitle = useCallback(
    (nodeId: string) => {
      const nodes = plan?.graph.nodes ?? []
      return nodes.find((node) => node.id === nodeId)?.name || nodeId
    },
    [plan],
  )

  const groups = useMemo(
    () => groupByNode(plan?.substitutions ?? [], nodeTitle),
    [plan, nodeTitle],
  )
  const counts = countPlan(plan)
  const dropped = pruneWarningCounts(plan)

  async function handlePromoteAndSave() {
    if (!caseId || !plan) return
    if (!selectionIsValid(plan, selected)) {
      setError(t('promote.staleSelection'))
      return
    }
    setSaving(true)
    setError(null)
    try {
      const result = await promoteRecording(caseId, {
        accept: selected,
        pruneToPath: prune,
        name: caseName || plan.suggestedName,
      })
      if (!result.graph) {
        setError(result.compile.issues[0] ?? t('promote.compileFailed'))
        return
      }
      // 图 id 由服务端分配（storage/memory.py:41-45 `graph-N`），前端不自己起名
      const saved = await saveGraph(result.graph)
      onSaved?.(saved.id)
      onClose()
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setSaving(false)
    }
  }

  async function handleLoadGuide() {
    if (!caseId) return
    setGuideBusy(true)
    setError(null)
    try {
      setGuide(await getRecordingGuide(caseId, lang))
    } catch (cause: unknown) {
      setError(cause instanceof Error ? cause.message : String(cause))
    } finally {
      setGuideBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      onCancel={onClose}
      width={820}
      title={t('promote.title', { name: caseName })}
      footer={
        <Space>
          <Button onClick={onClose}>{t('promote.close')}</Button>
          <Button
            type="primary"
            disabled={!plan || (!selected.length && !prune) || saving}
            loading={saving}
            onClick={handlePromoteAndSave}
          >
            {t('promote.save')}
          </Button>
        </Space>
      }
    >
      <Tabs
        items={[
          {
            key: 'promote',
            label: t('promote.tabPromote'),
            children: (
              <Spin spinning={planLoading}>
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <Typography.Text type="secondary">{t('promote.intro')}</Typography.Text>
                  {error && <Alert type="error" showIcon message={error} />}

                  <div>
                    <Typography.Text strong>{t('promote.proposals', { count: counts.proposals })}</Typography.Text>
                    {!groups.length && (
                      <div>
                        <Typography.Text type="secondary">{t('promote.noProposals')}</Typography.Text>
                      </div>
                    )}
                    {groups.map((group) => (
                      <div key={group.nodeId} style={{ marginTop: 8 }}>
                        <Typography.Text strong>{group.nodeTitle}</Typography.Text>
                        {group.items.map((item) => (
                          <div key={item.id} style={{ marginLeft: 8 }}>
                            <Checkbox
                              checked={selected.includes(item.id)}
                              onChange={(event) =>
                                setSelected((prev) => toggleSelection(prev, item.id, event.target.checked))
                              }
                            >
                              <Typography.Text code>{item.from}</Typography.Text>
                              <Typography.Text> → </Typography.Text>
                              <Typography.Text code>{item.to}</Typography.Text>
                            </Checkbox>
                            <div style={{ marginLeft: 24 }}>
                              <Typography.Text type="secondary">{item.excerpt}</Typography.Text>
                            </div>
                          </div>
                        ))}
                      </div>
                    ))}
                  </div>

                  {counts.ambiguous > 0 && (
                    <div>
                      <Typography.Text strong>{t('promote.ambiguous', { count: counts.ambiguous })}</Typography.Text>
                      {plan?.ambiguous.map((item) => (
                        <div key={item.id} style={{ marginLeft: 8 }}>
                          <Tag color="orange">{item.value}</Tag>
                          <Typography.Text type="secondary">
                            {item.candidates.join(' / ')} · {item.excerpt}
                          </Typography.Text>
                        </div>
                      ))}
                      <div>
                        <Typography.Text type="secondary">{t('promote.ambiguousHint')}</Typography.Text>
                      </div>
                    </div>
                  )}

                  {counts.skipped > 0 && (
                    <Typography.Text type="secondary">
                      {t('promote.skipped', { count: counts.skipped })}
                    </Typography.Text>
                  )}

                  <Space align="center">
                    <Switch
                      checked={prune}
                      aria-label={t('promote.prune', { dropped })}
                      onChange={setPrune}
                    />
                    <Typography.Text>{t('promote.prune', { dropped })}</Typography.Text>
                  </Space>

                  {plan && !plan.compile.ok && (
                    <Alert
                      type="warning"
                      showIcon
                      message={t('promote.planNotCompilable')}
                      description={plan.compile.issues[0]}
                    />
                  )}
                </Space>
              </Spin>
            ),
          },
          {
            key: 'guide',
            label: t('promote.tabGuide'),
            children: (
              <Spin spinning={guideBusy}>
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  {!guide && (
                    <Button onClick={handleLoadGuide} disabled={!caseId}>
                      {t('promote.guideLoad')}
                    </Button>
                  )}
                  {guide && (
                    <>
                      <Space>
                        <Button
                          onClick={() => {
                            const blob = new Blob([guide.markdown], { type: 'text/markdown;charset=utf-8' })
                            const url = URL.createObjectURL(blob)
                            const link = document.createElement('a')
                            link.href = url
                            link.download = guideFilename(guide.caseId, guide.lang)
                            link.click()
                            URL.revokeObjectURL(url)
                          }}
                        >
                          {t('promote.guideDownload')}
                        </Button>
                        <Typography.Text type="secondary">
                          {guide.fromRecordedPath
                            ? t('promote.guideFromPath')
                            : t('promote.guideFromGraph')}
                        </Typography.Text>
                      </Space>
                      <pre
                        style={{
                          maxHeight: 380,
                          overflow: 'auto',
                          padding: 12,
                          border: '1px solid var(--atlas-color-border, #d9d9d9)',
                          borderRadius: 8,
                          whiteSpace: 'pre-wrap',
                        }}
                      >
                        {guide.markdown}
                      </pre>
                    </>
                  )}
                </Space>
              </Spin>
            ),
          },
        ]}
      />
    </Modal>
  )
}
