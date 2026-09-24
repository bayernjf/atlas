import { useEffect, useState } from 'react'
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Collapse,
  Layout,
  Modal,
  Popconfirm,
  Select,
  Space,
  Tabs,
  Typography,
  Input,
  Tag,
} from 'antd'
import { FlowCanvas } from '../components/canvas/FlowCanvas'
import { NodePanel } from '../components/nodePanel/NodePanel'
import { VariablesPanel } from '../components/variablePanel/VariablesPanel'
import { PropertyPanel } from '../components/propertyPanel/PropertyPanel'
import { DebugConsole } from '../components/debugConsole/DebugConsole'
import { FeedbackButton } from '../components/feedback/FeedbackButton'
import { UserBadge } from '../components/UserBadge'
import { ApprovalCardGate } from '../components/approval/CardRenderer'
import { ReleaseModal } from '../components/release/ReleaseModal'
import { RolloutModal } from '../components/release/RolloutModal'
import { ShadowRunModal } from '../components/shadow/ShadowRunModal'
import { roleCan, type Principal } from '../lib/auth'
import { useTranslation } from '../locales'
import { useEditorStore } from '../store/editorStore'
import { useValidationEngine } from '../lib/validation/useValidationEngine'
import { useValidationStore } from '../store/validationStore'
import { serializeGraph } from '../lib/graphSerializer'
import { toSteps } from '../lib/recordings'
import { resolveExpressionErrors } from '../lib/runtimeError'
import { isSubgraphInternal, subgraphPathPrefix, subgraphPathLabel } from '../lib/subgraphEvents'
import { parseGlobalsDraft } from '../lib/debugOverrides'
import {
  compileGraph,
  CompileValidationError,
  decideApproval,
  decideCardAction,
  deleteRecording,
  getRecording,
  getTemplate,
  listRecordings,
  listTemplates,
  listVersions,
  nlGenerate,
  replayRecording,
  updateRecording,
  saveGraph,
  saveGraphDraft,
  saveRecording,
  streamRun,
  resumeDebug,
  cancelActiveRun,
  DebugRunStoppedError,
  RunCancelledError,
  type ApprovalRequest,
  type CompileResult,
  type DebugAction,
  type PausedFrame,
  type RecordingCase,
  type RecordingSummary,
  type ReplayReport,
  type ReplayRequestOptions,
  type RunEvent,
  type RunInputs,
  type RunResult,
  type TemplateSummary,
} from '../lib/apiClient'

const { Header, Sider, Content, Footer } = Layout
const { TextArea } = Input

/**
 * docs/28 §2.2/§2.3：把 TextArea 文本解析为顶层 JSON 对象；非法返回 ok:false 与
 * editor namespace 下的 i18n key（调用方经 t() 上屏，后端错误码 i18n 不在本批）。
 */
function parseInputsObject(
  text: string,
): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { ok: false, error: 'error.inputsInvalidJson' }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { ok: false, error: 'error.inputsNotObject' }
  }
  return { ok: true, value: parsed as Record<string, unknown> }
}

const DEMO_ORDERS: Array<{ order_id: string; reason: string; amount: number }> = [
  { order_id: '12345', reason: '商品破损', amount: 299 },
  { order_id: '12346', reason: '不想要了', amount: 5000 },
  { order_id: '12347', reason: '商品有质量瑕疵', amount: 128 },
  { order_id: '12348', reason: '商家错发商品', amount: 460 },
  { order_id: '12349', reason: '尺寸不合适', amount: 899 },
]

export function Editor({ principal, onLogout }: { principal: Principal; onLogout: () => void }) {
  const { t } = useTranslation('editor')
  const canOperate = roleCan(principal.role, 'operate')
  // docs/28 §3：调试暂停原因中文映射（含批 2 异常断点）；未知 reason 回退原值（后端枚举数据不译）。
  const reasonLabel = (reason: string): string =>
    t(`debug.reason.${reason}`, { defaultValue: reason })
  const nodes = useEditorStore((state) => state.nodes)
  const edges = useEditorStore((state) => state.edges)
  const variables = useEditorStore((state) => state.variables)
  const loadGraph = useEditorStore((state) => state.loadGraph)
  const setNodeStatus = useEditorStore((state) => state.setNodeStatus)
  const resetRunStatuses = useEditorStore((state) => state.resetRunStatuses)
  const appendLog = useEditorStore((state) => state.appendLog)
  const setNlWarnings = useEditorStore((state) => state.setNlWarnings)
  const breakpoints = useEditorStore((state) => state.breakpoints)

  // M4 批 2 ⑦：分层校验调度（L1 同步 / L2 防抖 / L3 idle），结果入 validationStore。
  useValidationEngine()

  // docs/61 §2.2：后端编译 422 的逐条诊断快照通道（Problems 面板消费）。
  const setServerIssues = useValidationStore((state) => state.setServerIssues)
  const clearServerIssues = useValidationStore((state) => state.clearServerIssues)

  const [exportOpen, setExportOpen] = useState(false)
  const [runOpen, setRunOpen] = useState(false)
  const [nlOpen, setNlOpen] = useState(false)
  const [templateOpen, setTemplateOpen] = useState(false)
  const [running, setRunning] = useState(false)
  const [nlLoading, setNlLoading] = useState(false)
  const [templates, setTemplates] = useState<TemplateSummary[]>([])
  const [templatesLoading, setTemplatesLoading] = useState(false)
  const [applyingTemplateId, setApplyingTemplateId] = useState<string | null>(null)
  const [templateError, setTemplateError] = useState<string | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [nlError, setNlError] = useState<string | null>(null)
  const [compileResult, setCompileResult] = useState<CompileResult | null>(null)
  const [runResult, setRunResult] = useState<RunResult | null>(null)
  const [pendingApprovals, setPendingApprovals] = useState<
    Array<ApprovalRequest & { nodeId: string; subgraphPath?: string[] }>
  >([])
  const [approvalBusy, setApprovalBusy] = useState(false)
  const [approvalError, setApprovalError] = useState<string | null>(null)
  const [selectedOrderId, setSelectedOrderId] = useState('12345')
  const [nlPrompt, setNlPrompt] = useState(t('nl.defaultPrompt'))
  const [recordingOpen, setRecordingOpen] = useState(false)
  const [recordings, setRecordings] = useState<RecordingSummary[]>([])
  const [recordingsLoading, setRecordingsLoading] = useState(false)
  const [recordingError, setRecordingError] = useState<string | null>(null)
  const [caseName, setCaseName] = useState('')
  const [recordBusy, setRecordBusy] = useState(false)
  const [replayBusyId, setReplayBusyId] = useState<string | null>(null)
  const [deletingId, setDeletingId] = useState<string | null>(null)
  const [reports, setReports] = useState<Record<string, ReplayReport>>({})
  // docs/28 §2.2：单用例回放 Mock 勾选 / 入参覆写草稿（per-case，一次性不落库）
  const [mockToolsById, setMockToolsById] = useState<Record<string, boolean>>({})
  const [overrideById, setOverrideById] = useState<Record<string, string>>({})
  // docs/28 §2.3：用例元信息编辑（仅 name/inputs）
  const [editingId, setEditingId] = useState<string | null>(null)
  const [loadingEditId, setLoadingEditId] = useState<string | null>(null)
  const [editName, setEditName] = useState('')
  const [editInputsText, setEditInputsText] = useState('')
  const [editError, setEditError] = useState<string | null>(null)
  const [savingEdit, setSavingEdit] = useState(false)
  const [pausedFrame, setPausedFrame] = useState<PausedFrame | null>(null)
  const [resumeBusy, setResumeBusy] = useState<DebugAction | null>(null)
  const [varFilter, setVarFilter] = useState('')
  // B 包（docs/27 §4.3）：暂停 Modal 内 globals 可编辑草稿，step/continue 浅合并写回。
  const [globalsDraft, setGlobalsDraft] = useState('')
  const [globalsError, setGlobalsError] = useState<string | null>(null)

  // 切换到新的暂停 token 时用最新只读快照预填草稿；编辑过程中不覆盖。
  const pausedToken = pausedFrame?.token
  useEffect(() => {
    if (pausedFrame) {
      setGlobalsDraft(JSON.stringify(pausedFrame.globals, null, 2))
      setGlobalsError(null)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pausedToken])
  const [releaseOpen, setReleaseOpen] = useState(false)
  const [rolloutOpen, setRolloutOpen] = useState(false)
  const [releaseGraphId, setReleaseGraphId] = useState<string | null>(null)
  const [rolloutGraphId, setRolloutGraphId] = useState<string | null>(null)
  const [shadowOpen, setShadowOpen] = useState(false)
  const [shadowGraphId, setShadowGraphId] = useState<string | null>(null)
  const [publishedRef, setPublishedRef] = useState<{ id: string; versions: number[] } | null>(null)
  const [draftGraphId, setDraftGraphId] = useState<string | null>(null)
  const [runTarget, setRunTarget] = useState<'draft' | number>('draft')
  const [releaseBusy, setReleaseBusy] = useState(false)

  const graphJson = JSON.stringify(serializeGraph(nodes, edges, variables, breakpoints), null, 2)

  async function compileAndRun(shouldRecord = false, debugMode = false) {
    const order = DEMO_ORDERS.find((item) => item.order_id === selectedOrderId)
    const inputs: RunInputs | undefined = order
      ? { order_id: order.order_id, reason: order.reason, amount: order.amount }
      : undefined
    const debugRequest = debugMode
      ? {
          breakpoints: Object.entries(breakpoints)
            .filter(([nodeId]) => nodes.some((node) => node.id === nodeId))
            .map(([node_id, breakpoint]) => {
              const expression = breakpoint.expression?.trim()
              const logMessage = breakpoint.logMessage?.trim()
              return {
                node_id,
                ...(expression ? { expression } : {}),
                ...(breakpoint.hitCount && breakpoint.hitCount >= 1
                  ? { hitCount: breakpoint.hitCount }
                  : {}),
                ...(logMessage ? { logMessage } : {}),
                ...(breakpoint.onException ? { onException: true } : {}),
              }
            }),
        }
      : undefined
    setRunning(true)
    setRunError(null)
    setCompileResult(null)
    // 每次尝试都从零开始：上一轮的后端编译诊断不得残留（docs/61 §2.2）。
    clearServerIssues()
    setRunResult(null)
    setPendingApprovals([])
    setApprovalError(null)
    setPausedFrame(null)
    setResumeBusy(null)
    setVarFilter('')
    resetRunStatuses()
    const collected: RunEvent[] = []
    try {
      const serialized = serializeGraph(nodes, edges, variables, breakpoints)
      const pinnedVersion = runTarget === 'draft' ? undefined : runTarget
      let graphId: string
      if (pinnedVersion !== undefined && publishedRef) {
        graphId = publishedRef.id
        appendLog(t('log.runPublished', { graphId, version: pinnedVersion }))
      } else if (draftGraphId) {
        // 同一画布复用稳定 graph id：PUT 覆盖草稿、不新建图，录制用例与发布门禁才能匹配本图
        await saveGraphDraft(draftGraphId, serialized)
        graphId = draftGraphId
        appendLog(t('log.draftUpdated', { graphId }))
        const compiled = await compileGraph(graphId)
        setCompileResult(compiled)
        appendLog(t('log.compileSuccess', { entrypoints: compiled.entrypoints.join(', ') }))
      } else {
        const saved = await saveGraph(serialized)
        graphId = saved.id
        setDraftGraphId(graphId)
        appendLog(t('log.graphSaved', { graphId }))
        const compiled = await compileGraph(graphId)
        setCompileResult(compiled)
        appendLog(t('log.compileSuccess', { entrypoints: compiled.entrypoints.join(', ')}))
      }
      const executed = await streamRun(
        graphId,
        inputs,
        (event) => {
        collected.push(event)
        if (event.type === 'paused') {
          setNodeStatus(event.node_id, 'paused')
          setPausedFrame(event)
          setResumeBusy(null)
          setVarFilter('')
          appendLog(
            t('log.paused', {
              prefix: subgraphPathPrefix(event.subgraphPath),
              node: event.node_id,
              reason: reasonLabel(event.reason),
            }),
          )
        } else if (event.type === 'stopped') {
          setPausedFrame(null)
          setNodeStatus(event.node_id, 'idle')
        } else if (event.type === 'cancelled') {
          // 普通流急停终帧：复位暂停态；提示语在 catch RunCancelledError 统一记，避免重复。
          setPausedFrame(null)
        } else if (event.type === 'debug_log') {
          const logPrefix = subgraphPathPrefix(event.subgraphPath)
          appendLog(
            t('log.logBreakpoint', {
              prefix: logPrefix,
              node: event.node_id,
              hits: event.hits,
              message: event.message,
            }),
          )
        } else if (event.type === 'node_start') {
          // A 包（docs/27 §3.3）：子图内部节点事件带 subgraphPath，日志加路径前缀，
          // 不写父图节点状态表（父 subgraph 节点由其自身无路径事件驱动高亮）。
          const startPath = event.subgraphPath ?? []
          const startInSubgraph = isSubgraphInternal(event.subgraphPath)
          const startPrefix = subgraphPathPrefix(event.subgraphPath)
          if (!startInSubgraph) setNodeStatus(event.node_id, 'running')
          appendLog(t('log.nodeStart', { prefix: startPrefix, node: event.node_id }))
          if (event.approval) {
            const approval = event.approval
            setPendingApprovals((items) => [
              ...items,
              { ...approval, nodeId: event.node_id, subgraphPath: startPath },
            ])
            appendLog(
              t('approval.waitingLog', {
                prefix: startPrefix,
                node: event.node_id,
                summary: approval.summary,
              }),
            )
          }
        } else if (event.type === 'node_end') {
          const endInSubgraph = isSubgraphInternal(event.subgraphPath)
          const endPrefix = subgraphPathPrefix(event.subgraphPath)
          if (!endInSubgraph) setNodeStatus(event.node_id, 'completed')
          const output = event.output as {
            decision?: { action?: string }
            branch?: string
            mode?: string
            iterations?: number
            exitReason?: string | null
            expression_errors?: string[]
            expressionErrorCodes?: string[]
            target?: string
            status?: string
            durationSeconds?: number
            resolvedBy?: string
            graphId?: string
            error?: string
            branches?: Array<{ label: string; target: string; status: string; error: string }>
            action_status?: string
            result?: { status?: unknown; code?: string; message?: string }
          }
          // A 包：子图内部节点只记带路径前缀的日志，不进入父图节点的富模式渲染；
          // 子图内审批仍要消除其 pending Modal（按内部 node_id 匹配）。
          if (endInSubgraph) {
            if (output?.mode === 'human_approval') {
              const subHumanDecision = output.decision as unknown as 'approved' | 'rejected'
              const subDecisionLabel =
                subHumanDecision === 'approved'
                  ? t('approval.decisionApproved')
                  : t('approval.decisionRejected')
              const subSourceLabel =
                {
                  human: t('approval.sourceHuman'),
                  timeout: t('approval.sourceTimeout'),
                  input: t('approval.sourceInput'),
                }[output.resolvedBy ?? ''] ?? output.resolvedBy
              appendLog(
                t('approval.resultLog', {
                  prefix: endPrefix,
                  node: event.node_id,
                  decision: subDecisionLabel,
                  source: subSourceLabel,
                  target: output.target,
                }),
              )
              setPendingApprovals((items) => items.filter((item) => item.nodeId !== event.node_id))
              setApprovalError(null)
            } else {
              appendLog(t('log.nodeComplete', { prefix: endPrefix, node: event.node_id }))
            }
            return
          }
          const decision = output?.decision
          if (output?.mode === 'human_approval') {
            const humanDecision = output.decision as unknown as 'approved' | 'rejected'
            const decisionText =
              humanDecision === 'approved'
                ? t('approval.decisionApproved')
                : t('approval.decisionRejected')
            const sourceText =
              {
                human: t('approval.sourceHuman'),
                timeout: t('approval.sourceTimeout'),
                input: t('approval.sourceInput'),
              }[output.resolvedBy ?? ''] ?? output.resolvedBy
            appendLog(
              t('approval.resultLog', {
                prefix: '',
                node: event.node_id,
                decision: decisionText,
                source: sourceText,
                target: output.target,
              }),
            )
            setPendingApprovals((items) => items.filter((item) => item.nodeId !== event.node_id))
            setApprovalError(null)
          } else if (decision?.action) {
            appendLog(t('log.decision', { node: event.node_id, action: decision.action }))
          } else if (output?.branch) {
            const branchText =
              output.branch === '__default__' ? t('log.branchDefault') : output.branch
            appendLog(
              t('log.branch', { node: event.node_id, branch: branchText, target: output.target }),
            )
          } else if (output?.mode === 'parallel') {
            if (output.status === 'running') {
              appendLog(
                t('log.parallelStarted', {
                  node: event.node_id,
                  count: output.branches?.length ?? 0,
                }),
              )
            } else if (output.status === 'failed') {
              const failed = (output.branches ?? []).filter((branch) => branch.status === 'failed')
              const detail = failed.map((branch) => `${branch.label}（${branch.error}）`).join('，')
              appendLog(
                t('log.parallelFailed', {
                  node: event.node_id,
                  count: failed.length,
                  detail,
                }),
              )
            } else {
              appendLog(t('log.parallelAllOk', { node: event.node_id }))
            }
          } else if (output?.mode === 'while') {
            if (output.exitReason === null) {
              appendLog(
                t('log.loopContinue', {
                  node: event.node_id,
                  iterations: output.iterations,
                  target: output.target,
                }),
              )
            } else {
              const loopReasonText =
                {
                  condition_false: t('log.loopReasonConditionFalse'),
                  max_iterations: t('log.loopReasonMaxIterations'),
                  expression_error: t('log.loopReasonExpressionError'),
                }[output.exitReason ?? ''] ?? output.exitReason
              appendLog(
                t('log.loopExit', {
                  node: event.node_id,
                  reason: loopReasonText,
                  iterations: output.iterations,
                  target: output.target,
                }),
              )
              // docs/60 G1：表达式错误退出时，按 expressionErrorCodes 解析当前语言明细；
              // 无码条目回退后端中文 expression_errors（不泄漏 i18n key）。
              if (output.exitReason === 'expression_error') {
                resolveExpressionErrors(
                  output.expressionErrorCodes,
                  output.expression_errors,
                ).forEach((detail) => {
                  if (detail) appendLog(`  ${detail}`)
                })
              }
            }
          } else if (output?.mode === 'wait') {
            appendLog(
              t('log.waitDone', { node: event.node_id, seconds: output.durationSeconds }),
            )
          } else if (output?.mode === 'subgraph') {
            if (output.status === 'failed') {
              appendLog(
                t('log.subgraphFailed', {
                  node: event.node_id,
                  graphId: output.graphId,
                  error: output.error ?? t('log.unknownError'),
                }),
              )
            } else {
              appendLog(
                t('log.subgraphOk', { node: event.node_id, graphId: output.graphId }),
              )
            }
          } else if (output?.action_status) {
            if (output.action_status === 'SUCCESS') {
              const httpStatus =
                typeof output.result?.status === 'number'
                  ? t('log.toolHttpStatus', { status: output.result.status })
                  : ''
              appendLog(t('log.toolSuccess', { node: event.node_id, http: httpStatus }))
            } else {
              appendLog(
                t('log.toolFailed', {
                  node: event.node_id,
                  code: output.result?.code ?? 'UNKNOWN',
                  message: output.result?.message ?? '',
                }),
              )
            }
          } else {
            appendLog(t('log.rootComplete', { node: event.node_id }))
          }
        }
      },
        debugRequest,
        pinnedVersion !== undefined ? { releaseVersion: pinnedVersion } : undefined,
      )
      const toolOutputs = Object.values(executed.outputs).filter(
        (output): output is { result?: { status?: string } } =>
          typeof output === 'object' && output !== null && 'result' in output,
      )
      const finalStatus = toolOutputs.find((output) => output.result?.status)?.result?.status
      appendLog(t('log.runComplete', { status: finalStatus ?? executed.status }))
      setRunResult(executed)
      setPausedFrame(null)
      setRunOpen(true)
      if (shouldRecord) {
        const now = new Date().toLocaleString()
        const name = caseName.trim() || t('recording.defaultName', { graphId, time: now })
        const savedCase = await saveRecording({
          name,
          graph_id: graphId,
          inputs: inputs ?? null,
          steps: toSteps(collected),
          status: executed.status,
        })
        appendLog(
          t('recording.saved', { id: savedCase.id, steps: savedCase.steps.length }),
        )
        setRecordings(await listRecordings())
        setCaseName(t('recording.defaultNameTime', { time: now }))
      }
    } catch (error) {
      if (error instanceof DebugRunStoppedError) {
        appendLog(t('log.stopped', { node: error.nodeId }))
      } else if (error instanceof RunCancelledError) {
        appendLog(t('log.cancelled', { node: error.nodeId }))
      } else {
        const message = error instanceof Error ? error.message : String(error)
        setRunError(message)
        if (shouldRecord) setRecordingError(message)
        appendLog(t('log.runFailed', { message }))
        // docs/61 §2.2：编译/保存 422 额外带逐条可定位诊断，进 Problems 面板；
        // 上方 message 仍是后端错误拼成的单串，日志与 toast 形状零变化。
        if (error instanceof CompileValidationError) setServerIssues(error.issues)
      }
    } finally {
      setRunning(false)
      setRecordBusy(false)
      setResumeBusy(null)
      setPausedFrame(null)
    }
  }

  async function ensureGraphId(): Promise<string> {
    const current = serializeGraph(nodes, edges, variables, breakpoints)
    if (draftGraphId) {
      await saveGraphDraft(draftGraphId, current)
      appendLog(t('log.draftUpdated', { graphId: draftGraphId }))
      return draftGraphId
    }
    const saved = await saveGraph(current)
    setDraftGraphId(saved.id)
    setPublishedRef((prev) => prev ?? { id: saved.id, versions: [] })
    appendLog(t('log.graphSaved', { graphId: saved.id }))
    return saved.id
  }

  async function openRelease() {
    setReleaseBusy(true)
    try {
      setReleaseGraphId(await ensureGraphId())
      setReleaseOpen(true)
    } catch (error) {
      setRunError(error instanceof Error ? error.message : String(error))
    } finally {
      setReleaseBusy(false)
    }
  }

  async function openRollout() {
    setReleaseBusy(true)
    try {
      const id = await ensureGraphId()
      setDraftGraphId(id)
      setPublishedRef({ id, versions: await listVersions(id) })
      setRolloutGraphId(id)
      setRolloutOpen(true)
    } catch (error) {
      setRunError(error instanceof Error ? error.message : String(error))
    } finally {
      setReleaseBusy(false)
    }
  }

  async function openShadow() {
    setReleaseBusy(true)
    try {
      setShadowGraphId(await ensureGraphId())
      setShadowOpen(true)
    } catch (error) {
      setRunError(error instanceof Error ? error.message : String(error))
    } finally {
      setReleaseBusy(false)
    }
  }

  const onPublished = (version: number) => {
    const id = draftGraphId ?? publishedRef?.id
    if (!id) return
    setPublishedRef((prev) => {
      const base = prev && prev.id === id ? prev.versions : []
      return { id, versions: [...new Set([...base, version])].sort((a, b) => a - b) }
    })
  }

  async function generateDraft() {
    setNlLoading(true)
    setNlError(null)
    try {
      const { graph, paramWarnings } = await nlGenerate(nlPrompt)
      loadGraph(graph)
      setDraftGraphId(null)
      setPublishedRef(null)
      setRunTarget('draft')
      // loadGraph 会清空警告，故在其后写入；M3 表单化后按节点归到 params 根（04 §4.10）
      setNlWarnings(paramWarnings ?? [])
      setNlOpen(false)
      paramWarnings?.forEach((warning) =>
        appendLog(t('nl.paramWarning', { warning })),
      )
    } catch (error) {
      setNlError(error instanceof Error ? error.message : String(error))
    } finally {
      setNlLoading(false)
    }
  }

  async function openTemplateBrowser() {
    setTemplateOpen(true)
    setTemplateError(null)
    if (templates.length > 0) return
    setTemplatesLoading(true)
    try {
      setTemplates(await listTemplates())
    } catch (error) {
      setTemplateError(error instanceof Error ? error.message : String(error))
    } finally {
      setTemplatesLoading(false)
    }
  }

  async function applyTemplate(templateId: string) {
    setApplyingTemplateId(templateId)
    setTemplateError(null)
    try {
      const detail = await getTemplate(templateId)
      loadGraph(detail.graph)
      setDraftGraphId(null)
      setPublishedRef(null)
      setRunTarget('draft')
      appendLog(t('template.loaded', { name: detail.name, id: detail.id }))
      setTemplateOpen(false)
    } catch (error) {
      setTemplateError(error instanceof Error ? error.message : String(error))
    } finally {
      setApplyingTemplateId(null)
    }
  }

  async function openRecordings() {
    setRecordingOpen(true)
    setRecordingError(null)
    if (!caseName) setCaseName(t('recording.defaultNameTime', { time: new Date().toLocaleString() }))
    setRecordingsLoading(true)
    try {
      setRecordings(await listRecordings())
    } catch (error) {
      setRecordingError(error instanceof Error ? error.message : String(error))
    } finally {
      setRecordingsLoading(false)
    }
  }

  async function recordCurrentRun() {
    setRecordingError(null)
    setRecordBusy(true)
    await compileAndRun(true)
  }

  async function runReplay(caseId: string) {
    setRecordingError(null)
    setReplayBusyId(caseId)
    try {
      const body: ReplayRequestOptions = {}
      if (mockToolsById[caseId]) body.mock_tools = true
      const overrideText = (overrideById[caseId] ?? '').trim()
      if (overrideText) {
        const parsed = parseInputsObject(overrideText)
        if (!parsed.ok) {
          setRecordingError(t(parsed.error))
          return
        }
        body.inputs_override = parsed.value as RunInputs
      }
      const report = await replayRecording(caseId, body)
      setReports((prev) => ({ ...prev, [caseId]: report }))
    } catch (error) {
      setRecordingError(error instanceof Error ? error.message : String(error))
    } finally {
      setReplayBusyId(null)
    }
  }

  // docs/28 §2.3：展开编辑并拉完整用例预填 name/inputs（列表投影不含 inputs）
  async function openEdit(rec: RecordingSummary) {
    setRecordingError(null)
    setEditError(null)
    setEditingId(rec.id)
    setEditName(rec.name)
    setEditInputsText('')
    setLoadingEditId(rec.id)
    try {
      const full: RecordingCase = await getRecording(rec.id)
      setEditName(full.name)
      setEditInputsText(JSON.stringify(full.inputs ?? {}, null, 2))
    } catch (error) {
      setEditError(error instanceof Error ? error.message : String(error))
    } finally {
      setLoadingEditId(null)
    }
  }

  function cancelEdit() {
    setEditingId(null)
    setEditError(null)
  }

  async function saveEdit(caseId: string) {
    setEditError(null)
    const patch: { name?: string; inputs?: RunInputs } = {}
    const name = editName.trim()
    if (name) patch.name = name
    const inputsText = editInputsText.trim()
    if (inputsText) {
      const parsed = parseInputsObject(inputsText)
      if (!parsed.ok) {
        setEditError(t(parsed.error))
        return
      }
      patch.inputs = parsed.value as RunInputs
    }
    if (!patch.name && !patch.inputs) {
      setEditError(t('recording.editRequired'))
      return
    }
    setSavingEdit(true)
    try {
      await updateRecording(caseId, patch)
      setRecordings(await listRecordings())
      setEditingId(null)
    } catch (error) {
      setEditError(error instanceof Error ? error.message : String(error))
    } finally {
      setSavingEdit(false)
    }
  }

  async function removeRecording(caseId: string) {
    setRecordingError(null)
    setDeletingId(caseId)
    try {
      await deleteRecording(caseId)
      setRecordings(await listRecordings())
      setReports((prev) => {
        const next = { ...prev }
        delete next[caseId]
        return next
      })
    } catch (error) {
      setRecordingError(error instanceof Error ? error.message : String(error))
    } finally {
      setDeletingId(null)
    }
  }

  const currentApproval = pendingApprovals[0] ?? null

  async function resolveCurrentApproval(decision: 'approved' | 'rejected') {
    if (!currentApproval) return
    setApprovalBusy(true)
    setApprovalError(null)
    try {
      await decideApproval(currentApproval.token, decision)
      setPendingApprovals((items) => items.filter((item) => item.token !== currentApproval.token))
    } catch (error) {
      // 409：等待期间已超时自动决策；404：已被 reset 清理——保留弹窗供确认关闭
      setApprovalError(error instanceof Error ? error.message : String(error))
    } finally {
      setApprovalBusy(false)
    }
  }

  async function resolveCurrentCard(actionId: string, form: Record<string, string>) {
    if (!currentApproval) return
    setApprovalBusy(true)
    setApprovalError(null)
    try {
      await decideCardAction(currentApproval.token, actionId, form)
      setPendingApprovals((items) => items.filter((item) => item.token !== currentApproval.token))
    } catch (error) {
      // 同 resolveCurrentApproval：409 已超时决策 / 404 已清理，保留弹窗供确认关闭
      setApprovalError(error instanceof Error ? error.message : String(error))
    } finally {
      setApprovalBusy(false)
    }
  }

  function dismissCurrentApproval() {
    if (!currentApproval) return
    setApprovalError(null)
    setPendingApprovals((items) => items.filter((item) => item.token !== currentApproval.token))
  }

  function parseGlobalsOverride(): Record<string, unknown> | null {
    const result = parseGlobalsDraft(globalsDraft)
    if (!result.ok) {
      setGlobalsError(result.error)
      return null
    }
    setGlobalsError(null)
    return result.value
  }

  async function resumeCurrentDebug(action: DebugAction) {
    const frame = pausedFrame
    if (!frame) return
    let globals: Record<string, unknown> | undefined
    if (action !== 'stop') {
      const parsed = parseGlobalsOverride()
      if (parsed === null) return // JSON/键名校验未过，不发请求（后端会再校验一次）
      globals = parsed
    }
    setResumeBusy(action)
    try {
      await resumeDebug(frame.token, action, globals)
    } catch (error) {
      appendLog(
        t('log.resumeFailed', {
          message: error instanceof Error ? error.message : String(error),
        }),
      )
      setResumeBusy(null)
    }
  }

  // B 包（docs/27 §4.1）协作式急停：调试暂停中走 stop；普通在途运行调 cancel 端点，
  // 下一节点边界生效，不在 wait/approval/tool 阻塞中点强杀。
  async function handleEmergencyStop() {
    if (pausedFrame) {
      await resumeCurrentDebug('stop')
      return
    }
    const activeGraphId = draftGraphId ?? publishedRef?.id
    if (!activeGraphId) {
      appendLog(t('log.noActiveRun'))
      return
    }
    try {
      const cancelled = await cancelActiveRun(activeGraphId)
      if (cancelled) {
        appendLog(t('log.stopRequested', { runId: cancelled.runId }))
      } else {
        appendLog(t('log.noActiveRunMaybeEnded'))
      }
    } catch (error) {
      appendLog(
        t('log.stopFailed', {
          message: error instanceof Error ? error.message : String(error),
        }),
      )
    }
  }

  return (
    <Layout className="editor-layout">
      <Header className="editor-header">
        <Space align="center">
          <Typography.Title level={3} style={{ margin: 0 }}>
            {t('header.title')}
          </Typography.Title>
          <UserBadge principal={principal} onLogout={onLogout} />
        </Space>
        <Space>
          <Select
            value={selectedOrderId}
            onChange={setSelectedOrderId}
            style={{ width: 300 }}
            options={DEMO_ORDERS.map((order) => ({
              value: order.order_id,
              label: `${order.order_id}｜${order.reason}｜¥${order.amount}`,
            }))}
          />
          {canOperate && <Button onClick={() => setNlOpen(true)}>{t('header.nlGenerate')}</Button>}
          <Button onClick={openTemplateBrowser}>{t('header.newFromTemplate')}</Button>
          {canOperate && <Button onClick={openRecordings}>{t('header.recordings')}</Button>}
          <Button onClick={() => setExportOpen(true)}>{t('header.exportJson')}</Button>
          <FeedbackButton />
          {canOperate && (
            <>
              <Select
                value={runTarget}
                style={{ width: 130 }}
                onChange={(value) => setRunTarget(value as 'draft' | number)}
                options={[
                  { value: 'draft', label: t('header.draftRun') },
                  ...(publishedRef?.versions ?? []).map((v) => ({
                    value: v,
                    label: t('header.publishedRun', { version: v }),
                  })),
                ]}
              />
              <Button loading={releaseBusy} onClick={openRelease}>
                {t('header.publish')}
              </Button>
              <Button loading={releaseBusy} onClick={openRollout}>
                {t('header.rollout')}
              </Button>
              {canOperate && (
                <Button loading={releaseBusy} onClick={openShadow}>
                  {t('header.shadowRun')}
                </Button>
              )}
              <Button
                loading={running}
                disabled={runTarget !== 'draft'}
                onClick={() => compileAndRun(false, true)}
              >
                {t('header.debug')}
              </Button>
              <Button type="primary" loading={running} onClick={() => compileAndRun()}>
                {t('header.compileAndRun')}
              </Button>
              {running && (
                <Button danger onClick={handleEmergencyStop}>
                  {t('header.emergencyStop')}
                </Button>
              )}
            </>
          )}
        </Space>
      </Header>
      <Layout>
        <Sider width={280} theme="light" className="editor-sider">
          <Tabs
            defaultActiveKey="nodes"
            style={{ height: '100%' }}
            items={[
              { key: 'nodes', label: t('tabs.nodes'), children: <NodePanel /> },
              { key: 'variables', label: t('tabs.variables'), children: <VariablesPanel /> },
            ]}
          />
        </Sider>
        <Content className="editor-content">
          <FlowCanvas />
        </Content>
        <Sider width={320} theme="light" className="editor-sider">
          <PropertyPanel />
        </Sider>
      </Layout>
      <Footer className="editor-footer">
        <DebugConsole />
      </Footer>
      <Modal
        title={t('export.title')}
        open={exportOpen}
        onCancel={() => setExportOpen(false)}
        onOk={() => setExportOpen(false)}
        width={680}
      >
        <pre className="graph-json-preview">{graphJson}</pre>
      </Modal>
      <Modal
        title={t('runResult.title')}
        open={runOpen}
        onCancel={() => setRunOpen(false)}
        onOk={() => setRunOpen(false)}
        width={720}
      >
        {compileResult && (
          <div className="run-result-section">
            <Typography.Text strong>
              {t('runResult.compiled', {
                entrypoints: compileResult.entrypoints.join(', '),
                terminals: compileResult.terminals.join(', '),
              })}
            </Typography.Text>
            <pre className="graph-json-preview">{JSON.stringify(compileResult, null, 2)}</pre>
          </div>
        )}
        {runResult && (
          <div className="run-result-section">
            <Typography.Text strong>{t('runResult.status', { status: runResult.status })}</Typography.Text>
            <pre className="graph-json-preview">{JSON.stringify(runResult, null, 2)}</pre>
          </div>
        )}
      </Modal>
      <Modal
        title={t('nl.title')}
        open={nlOpen}
        onCancel={() => setNlOpen(false)}
        onOk={generateDraft}
        confirmLoading={nlLoading}
        okText={t('nl.submit')}
      >
        <TextArea
          rows={3}
          value={nlPrompt}
          onChange={(event) => setNlPrompt(event.target.value)}
        />
        {nlError && <Alert type="error" showIcon title={nlError} style={{ marginTop: 12 }} />}
      </Modal>
      <Modal
        title={t('template.title')}
        open={templateOpen}
        onCancel={() => setTemplateOpen(false)}
        footer={null}
        width={680}
      >
        <Alert
          type="warning"
          showIcon
          title={t('template.replaceWarning')}
          style={{ marginBottom: 12 }}
        />
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          {templatesLoading && (
            <Typography.Text type="secondary">{t('template.loading')}</Typography.Text>
          )}
          {templates.map((template) => (
            <div
              key={template.id}
              style={{
                display: 'flex',
                justifyContent: 'space-between',
                gap: 12,
                padding: 12,
                border: '1px solid var(--atlas-color-border)',
                borderRadius: 8,
              }}
            >
              <div>
                <Space size={8} wrap style={{ marginBottom: 4 }}>
                  <Typography.Text strong>{template.name}</Typography.Text>
                  {template.tags.map((tag) => (
                    <Tag key={tag}>{tag}</Tag>
                  ))}
                  <Typography.Text type="secondary">
                    {t('template.nodeCount', { count: template.node_count })}
                  </Typography.Text>
                </Space>
                <div>
                  <Typography.Text type="secondary">{template.description}</Typography.Text>
                </div>
              </div>
              <Button
                type="link"
                loading={applyingTemplateId === template.id}
                disabled={applyingTemplateId !== null}
                onClick={() => applyTemplate(template.id)}
              >
                {t('template.use')}
              </Button>
            </div>
          ))}
        </Space>
        {templateError && <Alert type="error" showIcon title={templateError} style={{ marginTop: 12 }} />}
      </Modal>
      <Modal
        title={t('recording.title')}
        open={recordingOpen}
        onCancel={() => setRecordingOpen(false)}
        footer={null}
        width={720}
      >
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          <Space orientation="vertical" size={4} style={{ width: '100%' }}>
            <Input
              value={caseName}
              onChange={(event) => setCaseName(event.target.value)}
              placeholder={t('recording.namePlaceholder')}
            />
            <Space size={8} wrap>
              <Button type="primary" loading={recordBusy} onClick={recordCurrentRun}>
                {t('recording.recordButton')}
              </Button>
              <Typography.Text type="secondary">
                {t('recording.recordHint')}
              </Typography.Text>
            </Space>
          </Space>
          {recordingError && <Alert type="error" showIcon title={recordingError} />}
          <Typography.Text strong>
            {t('recording.listTitle', { count: recordings.length })}
          </Typography.Text>
          {recordingsLoading && (
            <Typography.Text type="secondary">{t('common:status.loading')}</Typography.Text>
          )}
          {!recordingsLoading && recordings.length === 0 && (
            <Typography.Text type="secondary">{t('recording.empty')}</Typography.Text>
          )}
          {recordings.map((rec) => {
            const report = reports[rec.id]
            return (
              <div
                key={rec.id}
                style={{
                  padding: 12,
                  border: '1px solid var(--atlas-color-border)',
                  borderRadius: 8,
                }}
              >
                <div
                  style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}
                >
                  <div>
                    <Space size={8} wrap style={{ marginBottom: 4 }}>
                      <Typography.Text strong>{rec.name}</Typography.Text>
                      <Tag color={rec.status === 'completed' ? 'green' : 'default'}>
                        {rec.status}
                      </Tag>
                    </Space>
                    <div>
                      <Typography.Text type="secondary">
                        {t('recording.meta', {
                          nodes: rec.node_count,
                          steps: rec.step_count,
                          time: new Date(rec.created_at).toLocaleString(),
                        })}
                      </Typography.Text>
                    </div>
                  </div>
                  <Space>
                    <Button
                      type="link"
                      loading={replayBusyId === rec.id}
                      onClick={() => runReplay(rec.id)}
                    >
                      {t('recording.replay')}
                    </Button>
                    <Button
                      type="link"
                      loading={loadingEditId === rec.id}
                      onClick={() =>
                        editingId === rec.id ? cancelEdit() : openEdit(rec)
                      }
                    >
                      {editingId === rec.id
                        ? t('recording.collapse')
                        : t('recording.edit')}
                    </Button>
                    <Popconfirm
                      title={t('recording.deleteConfirm')}
                      okText={t('common:button.delete')}
                      okButtonProps={{ danger: true }}
                      cancelText={t('common:button.cancel')}
                      onConfirm={() => removeRecording(rec.id)}
                    >
                      <Button type="link" danger loading={deletingId === rec.id}>
                        {t('common:button.delete')}
                      </Button>
                    </Popconfirm>
                  </Space>
                </div>
                {editingId === rec.id ? (
                  <div
                    style={{
                      marginTop: 8,
                      padding: 10,
                      border: '1px dashed var(--atlas-color-border)',
                      borderRadius: 8,
                    }}
                  >
                    {loadingEditId === rec.id ? (
                      <Typography.Text type="secondary">
                        {t('recording.loadingCase')}
                      </Typography.Text>
                    ) : (
                      <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                        <Input
                          value={editName}
                          onChange={(event) => setEditName(event.target.value)}
                          placeholder={t('recording.namePlaceholder')}
                        />
                        <TextArea
                          autoSize={{ minRows: 2, maxRows: 6 }}
                          value={editInputsText}
                          onChange={(event) => setEditInputsText(event.target.value)}
                          placeholder={t('recording.inputsPlaceholder')}
                        />
                        {editError && <Alert type="error" showIcon title={editError} />}
                        <Space size={8} wrap>
                          <Button
                            type="primary"
                            size="small"
                            loading={savingEdit}
                            onClick={() => saveEdit(rec.id)}
                          >
                            {t('common:button.save')}
                          </Button>
                          <Button size="small" onClick={cancelEdit}>
                            {t('common:button.cancel')}
                          </Button>
                          <Typography.Text type="secondary">
                            {t('recording.editHint')}
                          </Typography.Text>
                        </Space>
                      </Space>
                    )}
                  </div>
                ) : (
                  <div style={{ marginTop: 8 }}>
                    <Checkbox
                      checked={!!mockToolsById[rec.id]}
                      onChange={(event) =>
                        setMockToolsById((prev) => ({
                          ...prev,
                          [rec.id]: event.target.checked,
                        }))
                      }
                    >
                      {t('recording.mockCheckbox')}
                    </Checkbox>
                    <TextArea
                      autoSize={{ minRows: 1, maxRows: 3 }}
                      style={{ marginTop: 4 }}
                      value={overrideById[rec.id] ?? ''}
                      onChange={(event) =>
                        setOverrideById((prev) => ({
                          ...prev,
                          [rec.id]: event.target.value,
                        }))
                      }
                      placeholder={t('recording.overridePlaceholder')}
                    />
                  </div>
                )}
                {report && (
                  <div style={{ marginTop: 8 }}>
                    <Space size={8} wrap>
                      <Tag color={report.matches ? 'green' : 'red'}>
                        {report.matches ? t('recording.match') : t('recording.mismatch')}
                      </Tag>
                      {report.mocked_tools && report.mocked_tools.length > 0 && (
                        <Tag color="blue" title={report.mocked_tools.join(', ')}>
                          {t('recording.mockToolsTag', { count: report.mocked_tools.length })}
                        </Tag>
                      )}
                      <Typography.Text type="secondary">
                        {t('recording.baselineReplay', {
                          baseline: report.baseline_status,
                          replay: report.replay_status,
                        })}
                      </Typography.Text>
                    </Space>
                    <div style={{ marginTop: 4 }}>
                      {report.steps.map((row) => (
                        <div key={row.node_id}>
                          <Typography.Text type={row.match ? undefined : 'danger'}>
                            {row.match ? '✓' : '✗'} {row.node_id}
                            {row.note ? `：${row.note}` : ''}
                            {row.diff_keys && row.diff_keys.length > 0
                              ? t('recording.stepDiffKeys', { keys: row.diff_keys.join(', ') })
                              : ''}
                          </Typography.Text>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </Space>
      </Modal>
      <Modal
        title={t('approval.title')}
        open={currentApproval !== null}
        onCancel={dismissCurrentApproval}
        mask={{ closable: false }}
        width={520}
        footer={
          currentApproval && !currentApproval.cardTemplateId
            ? [
                <Button
                  key="reject"
                  danger
                  loading={approvalBusy}
                  onClick={() => resolveCurrentApproval('rejected')}
                >
                  {t('approval.reject')}
                </Button>,
                <Button
                  key="approve"
                  type="primary"
                  loading={approvalBusy}
                  onClick={() => resolveCurrentApproval('approved')}
                >
                  {t('approval.approve')}
                </Button>,
              ]
            : null
        }
      >
        {currentApproval && (
          <Space orientation="vertical" size={8} style={{ width: '100%' }}>
            <div>
              <Typography.Text type="secondary">{t('approval.nodeLabel')}</Typography.Text>
              <div>
                {currentApproval.subgraphPath && currentApproval.subgraphPath.length > 0
                  ? `[${subgraphPathLabel(currentApproval.subgraphPath)}] ${currentApproval.nodeId}`
                  : currentApproval.nodeId}
              </div>
              {currentApproval.subgraphPath && currentApproval.subgraphPath.length > 0 && (
                <div>
                  <Typography.Text type="secondary">
                    {t('approval.subgraphLabel', {
                      label: subgraphPathLabel(currentApproval.subgraphPath),
                    })}
                  </Typography.Text>
                </div>
              )}
            </div>
            {currentApproval.cardTemplateId ? (
              <ApprovalCardGate
                key={currentApproval.token}
                approval={currentApproval}
                busy={approvalBusy}
                onCardDecided={resolveCurrentCard}
                onLegacyDecided={resolveCurrentApproval}
              />
            ) : (
              <div>
                <Typography.Text type="secondary">{t('approval.summaryLabel')}</Typography.Text>
                <div>{currentApproval.summary}</div>
              </div>
            )}
            <div>
              <Typography.Text type="secondary">{t('approval.approverLabel')}</Typography.Text>
              <div>{currentApproval.approver || t('approval.approverUnspecified')}</div>
            </div>
            <Typography.Text type="secondary">
              {t('approval.timeoutHint', { seconds: currentApproval.timeoutSeconds })}
            </Typography.Text>
            {approvalError && <Alert type="error" showIcon title={approvalError} />}
          </Space>
        )}
      </Modal>
      {runError && (
        <Alert
          type="error"
          showIcon
          title={t('error.compileRunFailed')}
          description={runError}
          closable
          onClose={() => setRunError(null)}
          style={{ position: 'fixed', top: 72, right: 24, zIndex: 1000, width: 420 }}
        />
      )}
      {pausedFrame && (
        <Card
          size="small"
          className="debug-toolbar"
          title={
            <Space size={8} wrap>
              <span>{t('debug.pausedTitle')}</span>
              {pausedFrame.subgraphPath && pausedFrame.subgraphPath.length > 0 && (
                <Tag color="geekblue">{subgraphPathLabel(pausedFrame.subgraphPath)}</Tag>
              )}
              <Tag color="orange">{pausedFrame.node_id}</Tag>
              <Tag color={pausedFrame.reason === 'exception' ? 'red' : 'default'}>
                {reasonLabel(pausedFrame.reason)}
              </Tag>
            </Space>
          }
        >
          <Space orientation="vertical" size={8} style={{ width: '100%' }}>
            <Space size={8} wrap>
              <Button
                size="small"
                type="primary"
                loading={resumeBusy === 'step'}
                disabled={resumeBusy !== null}
                onClick={() => resumeCurrentDebug('step')}
              >
                {t('debug.step')}
              </Button>
              <Button
                size="small"
                loading={resumeBusy === 'continue'}
                disabled={resumeBusy !== null}
                onClick={() => resumeCurrentDebug('continue')}
              >
                {t('debug.continue')}
              </Button>
              <Button
                size="small"
                danger
                loading={resumeBusy === 'stop'}
                disabled={resumeBusy !== null}
                onClick={() => resumeCurrentDebug('stop')}
              >
                {t('debug.stop')}
              </Button>
            </Space>
            <Input
              size="small"
              allowClear
              placeholder={t('debug.varFilterPlaceholder')}
              value={varFilter}
              onChange={(event) => setVarFilter(event.target.value)}
            />
            <div>
              <Typography.Text type="secondary">
                {t('debug.globalsHint')}
              </Typography.Text>
              <TextArea
                size="small"
                autoSize={{ minRows: 3, maxRows: 10 }}
                value={globalsDraft}
                status={globalsError ? 'error' : undefined}
                onChange={(event) => setGlobalsDraft(event.target.value)}
              />
              {globalsError && (
                <Typography.Text type="danger">{globalsError}</Typography.Text>
              )}
              {varFilter ? (
                <pre className="debug-toolbar-json">
                  {JSON.stringify(filterSnapshot(pausedFrame.globals, varFilter), null, 2)}
                </pre>
              ) : null}
            </div>
            <div>
              <Typography.Text type="secondary">{t('debug.outputsHint')}</Typography.Text>
              <pre className="debug-toolbar-json">
                {JSON.stringify(filterSnapshot(pausedFrame.outputs, varFilter), null, 2)}
              </pre>
            </div>
            {pausedFrame.reason === 'exception' && pausedFrame.error && (
              <Alert
                type="error"
                showIcon
                message={t('debug.exceptionTitle', { type: pausedFrame.error.type })}
                description={
                  <Space direction="vertical" size={0}>
                    <Typography.Text>{pausedFrame.error.message}</Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      {t('debug.exceptionHint')}
                    </Typography.Text>
                  </Space>
                }
              />
            )}
            {pausedFrame.history && pausedFrame.history.length > 0 && (
              <Collapse
                size="small"
                items={[
                  {
                    key: 'variable-history',
                    label: t('debug.historyTitle', { count: pausedFrame.history.length }),
                    children: pausedFrame.history.map((item) => (
                      <div key={item.seq} style={{ marginBottom: 8 }}>
                        <Space size={4} wrap>
                          <Tag color="orange">{item.node_id}</Tag>
                          <Tag>{reasonLabel(item.reason)}</Tag>
                          {item.since_nodes.length > 0 && (
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              {t('debug.historySince', { path: item.since_nodes.join(' → ') })}
                            </Typography.Text>
                          )}
                        </Space>
                        {item.changes.length === 0 ? (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            {t('debug.historyEmpty')}
                          </Typography.Text>
                        ) : (
                          <pre className="debug-toolbar-json" style={{ marginTop: 4 }}>
                            {item.changes
                              .map(
                                (change) =>
                                  `${change.key}: ${JSON.stringify(change.old)} → ${JSON.stringify(
                                    change.new,
                                  )}`,
                              )
                              .join('\n')}
                          </pre>
                        )}
                      </div>
                    )),
                  },
                ]}
              />
            )}
          </Space>
        </Card>
      )}
      <ReleaseModal
        open={releaseOpen}
        graphId={releaseGraphId}
        onClose={() => setReleaseOpen(false)}
        onPublished={onPublished}
      />
      <RolloutModal
        open={rolloutOpen}
        graphId={rolloutGraphId}
        tenant={principal.tenant_id}
        onClose={() => setRolloutOpen(false)}
      />
      <ShadowRunModal
        open={shadowOpen}
        graphId={shadowGraphId}
        onClose={() => setShadowOpen(false)}
      />
    </Layout>
  )
}

function filterSnapshot(
  snapshot: Record<string, unknown>,
  keyword: string,
): Record<string, unknown> {
  const kw = keyword.trim()
  if (!kw) return snapshot
  return Object.fromEntries(Object.entries(snapshot).filter(([key]) => key.includes(kw)))
}
