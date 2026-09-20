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
import { roleCan, type Principal } from '../lib/auth'
import { useEditorStore } from '../store/editorStore'
import { useValidationEngine } from '../lib/validation/useValidationEngine'
import { serializeGraph } from '../lib/graphSerializer'
import { toSteps } from '../lib/recordings'
import { isSubgraphInternal, subgraphPathPrefix, subgraphPathLabel } from '../lib/subgraphEvents'
import { parseGlobalsDraft } from '../lib/debugOverrides'
import {
  compileGraph,
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

/** docs/28 §2.2/§2.3：把 TextArea 文本解析为顶层 JSON 对象；非法返回 ok:false 与文案。 */
function parseInputsObject(
  text: string,
): { ok: true; value: Record<string, unknown> } | { ok: false; error: string } {
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { ok: false, error: '入参不是合法 JSON，请检查格式' }
  }
  if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
    return { ok: false, error: '入参必须是顶层 JSON 对象（{}）' }
  }
  return { ok: true, value: parsed as Record<string, unknown> }
}

// docs/28 §3：调试暂停原因中文映射（含批 2 异常断点）。
const DEBUG_REASON_LABELS: Record<string, string> = {
  step: '单步',
  breakpoint: '断点',
  condition: '条件',
  exception: '异常',
}

const DEMO_ORDERS: Array<{ order_id: string; reason: string; amount: number }> = [
  { order_id: '12345', reason: '商品破损', amount: 299 },
  { order_id: '12346', reason: '不想要了', amount: 5000 },
  { order_id: '12347', reason: '商品有质量瑕疵', amount: 128 },
  { order_id: '12348', reason: '商家错发商品', amount: 460 },
  { order_id: '12349', reason: '尺寸不合适', amount: 899 },
]

export function Editor({ principal, onLogout }: { principal: Principal; onLogout: () => void }) {
  const canOperate = roleCan(principal.role, 'operate')
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
  const [nlPrompt, setNlPrompt] = useState('帮我做一个电商退款自动审批流程')
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
  const [publishedRef, setPublishedRef] = useState<{ id: string; versions: number[] } | null>(null)
  const [draftGraphId, setDraftGraphId] = useState<string | null>(null)
  const [runTarget, setRunTarget] = useState<'draft' | number>('draft')
  const [releaseBusy, setReleaseBusy] = useState(false)

  const graphJson = JSON.stringify(serializeGraph(nodes, edges, variables), null, 2)

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
    setRunResult(null)
    setPendingApprovals([])
    setApprovalError(null)
    setPausedFrame(null)
    setResumeBusy(null)
    setVarFilter('')
    resetRunStatuses()
    const collected: RunEvent[] = []
    try {
      const serialized = serializeGraph(nodes, edges, variables)
      const pinnedVersion = runTarget === 'draft' ? undefined : runTarget
      let graphId: string
      if (pinnedVersion !== undefined && publishedRef) {
        graphId = publishedRef.id
        appendLog(`运行已发布版本：${graphId}@${pinnedVersion}（不使用当前草稿）`)
      } else if (draftGraphId) {
        // 同一画布复用稳定 graph id：PUT 覆盖草稿、不新建图，录制用例与发布门禁才能匹配本图
        await saveGraphDraft(draftGraphId, serialized)
        graphId = draftGraphId
        appendLog(`已更新草稿：${graphId}`)
        const compiled = await compileGraph(graphId)
        setCompileResult(compiled)
        appendLog(`编译成功：入口 ${compiled.entrypoints.join(', ')}`)
      } else {
        const saved = await saveGraph(serialized)
        graphId = saved.id
        setDraftGraphId(graphId)
        appendLog(`已保存 Graph：${graphId}`)
        const compiled = await compileGraph(graphId)
        setCompileResult(compiled)
        appendLog(`编译成功：入口 ${compiled.entrypoints.join(', ')}`)
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
          const reasonLabel = DEBUG_REASON_LABELS[event.reason] ?? event.reason
          appendLog(
            `⏸ 调试暂停：${subgraphPathPrefix(event.subgraphPath)}${event.node_id}（${reasonLabel}）`,
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
            `📝 ${logPrefix}${event.node_id} 日志断点（第 ${event.hits} 次）：${event.message}`,
          )
        } else if (event.type === 'node_start') {
          // A 包（docs/27 §3.3）：子图内部节点事件带 subgraphPath，日志加路径前缀，
          // 不写父图节点状态表（父 subgraph 节点由其自身无路径事件驱动高亮）。
          const startPath = event.subgraphPath ?? []
          const startInSubgraph = isSubgraphInternal(event.subgraphPath)
          const startPrefix = subgraphPathPrefix(event.subgraphPath)
          if (!startInSubgraph) setNodeStatus(event.node_id, 'running')
          appendLog(`▶ ${startPrefix}节点开始：${event.node_id}`)
          if (event.approval) {
            const approval = event.approval
            setPendingApprovals((items) => [
              ...items,
              { ...approval, nodeId: event.node_id, subgraphPath: startPath },
            ])
            appendLog(`⏸ ${startPrefix}${event.node_id} 等待人工审批：${approval.summary}`)
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
              const subDecisionLabel = subHumanDecision === 'approved' ? '通过' : '拒绝'
              const subSourceLabel =
                { human: '人工', timeout: '超时', input: '预置' }[output.resolvedBy ?? ''] ??
                output.resolvedBy
              appendLog(
                `✓ ${endPrefix}${event.node_id} 人工审批：${subDecisionLabel}（${subSourceLabel}）→ ${output.target}`,
              )
              setPendingApprovals((items) => items.filter((item) => item.nodeId !== event.node_id))
              setApprovalError(null)
            } else {
              appendLog(`✓ ${endPrefix}节点完成：${event.node_id}`)
            }
            return
          }
          const decision = output?.decision
          if (output?.mode === 'human_approval') {
            const humanDecision = output.decision as unknown as 'approved' | 'rejected'
            const decisionLabel = humanDecision === 'approved' ? '通过' : '拒绝'
            const sourceLabel =
              { human: '人工', timeout: '超时', input: '预置' }[output.resolvedBy ?? ''] ??
              output.resolvedBy
            appendLog(
              `✓ ${event.node_id} 人工审批：${decisionLabel}（${sourceLabel}）→ ${output.target}`,
            )
            setPendingApprovals((items) => items.filter((item) => item.nodeId !== event.node_id))
            setApprovalError(null)
          } else if (decision?.action) {
            appendLog(`✓ ${event.node_id} 决策：${decision.action}`)
          } else if (output?.branch) {
            const branchLabel = output.branch === '__default__' ? '默认' : output.branch
            appendLog(`✓ ${event.node_id} 分支：${branchLabel} → ${output.target}`)
          } else if (output?.mode === 'parallel') {
            if (output.status === 'running') {
              appendLog(`✓ ${event.node_id} 并行启动 ${output.branches?.length ?? 0} 个分支`)
            } else if (output.status === 'failed') {
              const failed = (output.branches ?? []).filter((branch) => branch.status === 'failed')
              const detail = failed.map((branch) => `${branch.label}（${branch.error}）`).join('，')
              appendLog(
                `✓ ${event.node_id} 并行汇聚：${failed.length} 个分支失败：${detail}（汇聚节点仍执行）`,
              )
            } else {
              appendLog(`✓ ${event.node_id} 并行汇聚：全部成功`)
            }
          } else if (output?.mode === 'while') {
            if (output.exitReason === null) {
              appendLog(`✓ ${event.node_id} 继续循环：第 ${output.iterations} 轮 → ${output.target}`)
            } else {
              const reasonLabel = {
                condition_false: '条件不满足',
                max_iterations: '达到最大次数',
                expression_error: '表达式异常',
              }[output.exitReason ?? ''] ?? output.exitReason
              appendLog(
                `✓ ${event.node_id} 退出循环：${reasonLabel}，共 ${output.iterations} 轮 → ${output.target}`,
              )
            }
          } else if (output?.mode === 'wait') {
            appendLog(`✓ ${event.node_id} 等待完成：${output.durationSeconds} 秒`)
          } else if (output?.mode === 'subgraph') {
            if (output.status === 'failed') {
              appendLog(`✓ ${event.node_id} 子图完成：${output.graphId}（失败：${output.error ?? '未知错误'}）`)
            } else {
              appendLog(`✓ ${event.node_id} 子图完成：${output.graphId}（成功）`)
            }
          } else if (output?.action_status) {
            if (output.action_status === 'SUCCESS') {
              const httpStatus =
                typeof output.result?.status === 'number' ? `（HTTP ${output.result.status}）` : ''
              appendLog(`✓ ${event.node_id} 工具调用：SUCCESS${httpStatus}`)
            } else {
              appendLog(
                `✗ ${event.node_id} 工具调用：FAILED（${output.result?.code ?? 'UNKNOWN'} ${output.result?.message ?? ''}）`,
              )
            }
          } else {
            appendLog(`✓ 节点完成：${event.node_id}`)
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
      appendLog(`运行结束：${finalStatus ?? executed.status}`)
      setRunResult(executed)
      setPausedFrame(null)
      setRunOpen(true)
      if (shouldRecord) {
        const name = caseName.trim() || `录制 ${graphId} ${new Date().toLocaleString()}`
        const savedCase = await saveRecording({
          name,
          graph_id: graphId,
          inputs: inputs ?? null,
          steps: toSteps(collected),
          status: executed.status,
        })
        appendLog(`已保存录制用例：${savedCase.id}（${savedCase.steps.length} 个步骤）`)
        setRecordings(await listRecordings())
        setCaseName(`录制 ${new Date().toLocaleString()}`)
      }
    } catch (error) {
      if (error instanceof DebugRunStoppedError) {
        appendLog(`调试已停止：${error.nodeId}（无运行结果）`)
      } else if (error instanceof RunCancelledError) {
        appendLog(`⏹ 运行已急停：${error.nodeId}（协作式取消，无运行结果）`)
      } else {
        const message = error instanceof Error ? error.message : String(error)
        setRunError(message)
        if (shouldRecord) setRecordingError(message)
        appendLog(`✗ 运行失败：${message}`)
      }
    } finally {
      setRunning(false)
      setRecordBusy(false)
      setResumeBusy(null)
      setPausedFrame(null)
    }
  }

  async function ensureGraphId(): Promise<string> {
    const current = serializeGraph(nodes, edges, variables)
    if (draftGraphId) {
      await saveGraphDraft(draftGraphId, current)
      appendLog(`已更新草稿：${draftGraphId}`)
      return draftGraphId
    }
    const saved = await saveGraph(current)
    setDraftGraphId(saved.id)
    setPublishedRef((prev) => prev ?? { id: saved.id, versions: [] })
    appendLog(`已保存 Graph：${saved.id}`)
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
      paramWarnings?.forEach((warning) => appendLog(`⚠ NL 参数提示：${warning}`))
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
      appendLog(`已加载模板：${detail.name}（${detail.id}），画布已整体替换`)
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
    if (!caseName) setCaseName(`录制 ${new Date().toLocaleString()}`)
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
          setRecordingError(parsed.error)
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
        setEditError(parsed.error)
        return
      }
      patch.inputs = parsed.value as RunInputs
    }
    if (!patch.name && !patch.inputs) {
      setEditError('请至少修改名称或入参之一')
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
      appendLog(`✗ 调试放行失败：${error instanceof Error ? error.message : String(error)}`)
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
      appendLog('当前没有在途运行可取消')
      return
    }
    try {
      const cancelled = await cancelActiveRun(activeGraphId)
      if (cancelled) {
        appendLog(`⏹ 已请求急停 ${cancelled.runId}（下一节点边界生效）`)
      } else {
        appendLog('当前没有在途运行可取消（可能已结束）')
      }
    } catch (error) {
      appendLog(`✗ 急停失败：${error instanceof Error ? error.message : String(error)}`)
    }
  }

  return (
    <Layout className="editor-layout">
      <Header className="editor-header">
        <Space align="center">
          <Typography.Title level={3} style={{ margin: 0 }}>
            Atlas 流程编辑器
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
          {canOperate && <Button onClick={() => setNlOpen(true)}>自然语言生成</Button>}
          <Button onClick={openTemplateBrowser}>从模板新建</Button>
          {canOperate && <Button onClick={openRecordings}>录制与回放</Button>}
          <Button onClick={() => setExportOpen(true)}>导出 Graph JSON</Button>
          <FeedbackButton />
          {canOperate && (
            <>
              <Select
                value={runTarget}
                style={{ width: 130 }}
                onChange={(value) => setRunTarget(value as 'draft' | number)}
                options={[
                  { value: 'draft', label: '草稿运行' },
                  ...(publishedRef?.versions ?? []).map((v) => ({ value: v, label: `已发布 v${v}` })),
                ]}
              />
              <Button loading={releaseBusy} onClick={openRelease}>
                发布
              </Button>
              <Button loading={releaseBusy} onClick={openRollout}>
                灰度发布
              </Button>
              <Button
                loading={running}
                disabled={runTarget !== 'draft'}
                onClick={() => compileAndRun(false, true)}
              >
                调试
              </Button>
              <Button type="primary" loading={running} onClick={() => compileAndRun()}>
                编译并运行
              </Button>
              {running && (
                <Button danger onClick={handleEmergencyStop}>
                  急停
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
              { key: 'nodes', label: '节点', children: <NodePanel /> },
              { key: 'variables', label: '变量', children: <VariablesPanel /> },
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
        title="Graph 定义 JSON（W7-W8 DSL 编译输入）"
        open={exportOpen}
        onCancel={() => setExportOpen(false)}
        onOk={() => setExportOpen(false)}
        width={680}
      >
        <pre className="graph-json-preview">{graphJson}</pre>
      </Modal>
      <Modal
        title="编译并运行结果"
        open={runOpen}
        onCancel={() => setRunOpen(false)}
        onOk={() => setRunOpen(false)}
        width={720}
      >
        {compileResult && (
          <div className="run-result-section">
            <Typography.Text strong>
              编译成功（{compileResult.entrypoints.join(', ')} →{' '}
              {compileResult.terminals.join(', ')}）
            </Typography.Text>
            <pre className="graph-json-preview">{JSON.stringify(compileResult, null, 2)}</pre>
          </div>
        )}
        {runResult && (
          <div className="run-result-section">
            <Typography.Text strong>运行状态：{runResult.status}</Typography.Text>
            <pre className="graph-json-preview">{JSON.stringify(runResult, null, 2)}</pre>
          </div>
        )}
      </Modal>
      <Modal
        title="自然语言生成流程草稿"
        open={nlOpen}
        onCancel={() => setNlOpen(false)}
        onOk={generateDraft}
        confirmLoading={nlLoading}
        okText="生成并载入画布"
      >
        <TextArea
          rows={3}
          value={nlPrompt}
          onChange={(event) => setNlPrompt(event.target.value)}
        />
        {nlError && <Alert type="error" showIcon title={nlError} style={{ marginTop: 12 }} />}
      </Modal>
      <Modal
        title="从模板新建"
        open={templateOpen}
        onCancel={() => setTemplateOpen(false)}
        footer={null}
        width={680}
      >
        <Alert
          type="warning"
          showIcon
          title="加载模板将整体替换当前画布，未保存的修改会丢失。"
          style={{ marginBottom: 12 }}
        />
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          {templatesLoading && <Typography.Text type="secondary">模板加载中…</Typography.Text>}
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
                  <Typography.Text type="secondary">{template.node_count} 个节点</Typography.Text>
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
                使用此模板
              </Button>
            </div>
          ))}
        </Space>
        {templateError && <Alert type="error" showIcon title={templateError} style={{ marginTop: 12 }} />}
      </Modal>
      <Modal
        title="操作录制与回放"
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
              placeholder="用例名称"
            />
            <Space size={8} wrap>
              <Button type="primary" loading={recordBusy} onClick={recordCurrentRun}>
                录制当前画布一次运行
              </Button>
              <Typography.Text type="secondary">
                按当前订单入参真实运行一次（审批弹窗照常交互），结束时冻结 Graph 快照入库
              </Typography.Text>
            </Space>
          </Space>
          {recordingError && <Alert type="error" showIcon title={recordingError} />}
          <Typography.Text strong>已录制用例（{recordings.length}）</Typography.Text>
          {recordingsLoading && <Typography.Text type="secondary">加载中…</Typography.Text>}
          {!recordingsLoading && recordings.length === 0 && (
            <Typography.Text type="secondary">暂无录制用例。</Typography.Text>
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
                        {rec.node_count} 节点 · {rec.step_count} 步骤 ·{' '}
                        {new Date(rec.created_at).toLocaleString()}
                      </Typography.Text>
                    </div>
                  </div>
                  <Space>
                    <Button
                      type="link"
                      loading={replayBusyId === rec.id}
                      onClick={() => runReplay(rec.id)}
                    >
                      回放
                    </Button>
                    <Button
                      type="link"
                      loading={loadingEditId === rec.id}
                      onClick={() =>
                        editingId === rec.id ? cancelEdit() : openEdit(rec)
                      }
                    >
                      {editingId === rec.id ? '收起' : '编辑'}
                    </Button>
                    <Popconfirm
                      title="确认删除该录制用例？"
                      okText="删除"
                      okButtonProps={{ danger: true }}
                      cancelText="取消"
                      onConfirm={() => removeRecording(rec.id)}
                    >
                      <Button type="link" danger loading={deletingId === rec.id}>
                        删除
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
                      <Typography.Text type="secondary">加载用例…</Typography.Text>
                    ) : (
                      <Space orientation="vertical" size={8} style={{ width: '100%' }}>
                        <Input
                          value={editName}
                          onChange={(event) => setEditName(event.target.value)}
                          placeholder="用例名称"
                        />
                        <TextArea
                          autoSize={{ minRows: 2, maxRows: 6 }}
                          value={editInputsText}
                          onChange={(event) => setEditInputsText(event.target.value)}
                          placeholder="回放入参（JSON 对象，保存时整体替换）"
                        />
                        {editError && <Alert type="error" showIcon title={editError} />}
                        <Space size={8} wrap>
                          <Button
                            type="primary"
                            size="small"
                            loading={savingEdit}
                            onClick={() => saveEdit(rec.id)}
                          >
                            保存
                          </Button>
                          <Button size="small" onClick={cancelEdit}>
                            取消
                          </Button>
                          <Typography.Text type="secondary">
                            仅名称/入参可改；步骤与 Graph 快照不可改（请重新录制）
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
                      Mock 工具节点（命中录制输出、不触达适配器；发布门禁不接 mock）
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
                      placeholder='入参覆写（可选，JSON 对象如 {"amount": 100}，仅本次回放浅合并、不落库）'
                    />
                  </div>
                )}
                {report && (
                  <div style={{ marginTop: 8 }}>
                    <Space size={8} wrap>
                      <Tag color={report.matches ? 'green' : 'red'}>
                        {report.matches ? '匹配' : '不匹配'}
                      </Tag>
                      {report.mocked_tools && report.mocked_tools.length > 0 && (
                        <Tag color="blue" title={report.mocked_tools.join(', ')}>
                          Mock {report.mocked_tools.length} 工具
                        </Tag>
                      )}
                      <Typography.Text type="secondary">
                        基线 {report.baseline_status} → 回放 {report.replay_status}
                      </Typography.Text>
                    </Space>
                    <div style={{ marginTop: 4 }}>
                      {report.steps.map((row) => (
                        <div key={row.node_id}>
                          <Typography.Text type={row.match ? undefined : 'danger'}>
                            {row.match ? '✓' : '✗'} {row.node_id}
                            {row.note ? `：${row.note}` : ''}
                            {row.diff_keys && row.diff_keys.length > 0
                              ? `（差异键：${row.diff_keys.join(', ')}）`
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
        title="人工审批请求"
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
                  拒绝
                </Button>,
                <Button
                  key="approve"
                  type="primary"
                  loading={approvalBusy}
                  onClick={() => resolveCurrentApproval('approved')}
                >
                  同意
                </Button>,
              ]
            : null
        }
      >
        {currentApproval && (
          <Space orientation="vertical" size={8} style={{ width: '100%' }}>
            <div>
              <Typography.Text type="secondary">节点</Typography.Text>
              <div>
                {currentApproval.subgraphPath && currentApproval.subgraphPath.length > 0
                  ? `[${subgraphPathLabel(currentApproval.subgraphPath)}] ${currentApproval.nodeId}`
                  : currentApproval.nodeId}
              </div>
              {currentApproval.subgraphPath && currentApproval.subgraphPath.length > 0 && (
                <div>
                  <Typography.Text type="secondary">
                    子图内审批（所属 subgraph 节点：{subgraphPathLabel(currentApproval.subgraphPath)}）
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
                <Typography.Text type="secondary">审批说明</Typography.Text>
                <div>{currentApproval.summary}</div>
              </div>
            )}
            <div>
              <Typography.Text type="secondary">审批人</Typography.Text>
              <div>{currentApproval.approver || '未指定'}</div>
            </div>
            <Typography.Text type="secondary">
              等待 {currentApproval.timeoutSeconds} 秒后按超时策略自动决策；关闭弹窗后仍可在等待期内由
              API 放行。
            </Typography.Text>
            {approvalError && <Alert type="error" showIcon title={approvalError} />}
          </Space>
        )}
      </Modal>
      {runError && (
        <Alert
          type="error"
          showIcon
          title="编译/运行失败"
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
              <span>调试暂停于</span>
              {pausedFrame.subgraphPath && pausedFrame.subgraphPath.length > 0 && (
                <Tag color="geekblue">{subgraphPathLabel(pausedFrame.subgraphPath)}</Tag>
              )}
              <Tag color="orange">{pausedFrame.node_id}</Tag>
              <Tag color={pausedFrame.reason === 'exception' ? 'red' : 'default'}>
                {DEBUG_REASON_LABELS[pausedFrame.reason] ?? pausedFrame.reason}
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
                下一步
              </Button>
              <Button
                size="small"
                loading={resumeBusy === 'continue'}
                disabled={resumeBusy !== null}
                onClick={() => resumeCurrentDebug('continue')}
              >
                继续
              </Button>
              <Button
                size="small"
                danger
                loading={resumeBusy === 'stop'}
                disabled={resumeBusy !== null}
                onClick={() => resumeCurrentDebug('stop')}
              >
                停止
              </Button>
            </Space>
            <Input
              size="small"
              allowClear
              placeholder="按键过滤变量"
              value={varFilter}
              onChange={(event) => setVarFilter(event.target.value)}
            />
            <div>
              <Typography.Text type="secondary">
                全局变量 globals（可编辑 JSON；下一步/继续时浅合并写回，停止忽略）
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
              <Typography.Text type="secondary">节点产出 outputs（只读快照）</Typography.Text>
              <pre className="debug-toolbar-json">
                {JSON.stringify(filterSnapshot(pausedFrame.outputs, varFilter), null, 2)}
              </pre>
            </div>
            {pausedFrame.reason === 'exception' && pausedFrame.error && (
              <Alert
                type="error"
                showIcon
                message={`异常断点捕获：${pausedFrame.error.type}`}
                description={
                  <Space direction="vertical" size={0}>
                    <Typography.Text>{pausedFrame.error.message}</Typography.Text>
                    <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                      下一步/继续后将原样抛出该异常（v1 不支持忽略继续），停止则结束本次调试。
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
                    label: `变量变化历史（${pausedFrame.history.length}）`,
                    children: pausedFrame.history.map((item) => (
                      <div key={item.seq} style={{ marginBottom: 8 }}>
                        <Space size={4} wrap>
                          <Tag color="orange">{item.node_id}</Tag>
                          <Tag>{DEBUG_REASON_LABELS[item.reason] ?? item.reason}</Tag>
                          {item.since_nodes.length > 0 && (
                            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                              经过节点 {item.since_nodes.join(' → ')}
                            </Typography.Text>
                          )}
                        </Space>
                        {item.changes.length === 0 ? (
                          <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                            本次暂停无 global 顶层键变化
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
