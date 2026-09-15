import { useState } from 'react'
import {
  Alert,
  Button,
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
import { useEditorStore } from '../store/editorStore'
import { serializeGraph } from '../lib/graphSerializer'
import { toSteps } from '../lib/recordings'
import {
  compileGraph,
  decideApproval,
  deleteRecording,
  getTemplate,
  listRecordings,
  listTemplates,
  nlGenerate,
  replayRecording,
  saveGraph,
  saveRecording,
  streamRun,
  type ApprovalRequest,
  type CompileResult,
  type RecordingSummary,
  type ReplayReport,
  type RunEvent,
  type RunInputs,
  type RunResult,
  type TemplateSummary,
} from '../lib/apiClient'

const { Header, Sider, Content, Footer } = Layout
const { TextArea } = Input

const DEMO_ORDERS: Array<{ order_id: string; reason: string; amount: number }> = [
  { order_id: '12345', reason: '商品破损', amount: 299 },
  { order_id: '12346', reason: '不想要了', amount: 5000 },
  { order_id: '12347', reason: '商品有质量瑕疵', amount: 128 },
  { order_id: '12348', reason: '商家错发商品', amount: 460 },
  { order_id: '12349', reason: '尺寸不合适', amount: 899 },
]

export function Editor() {
  const nodes = useEditorStore((state) => state.nodes)
  const edges = useEditorStore((state) => state.edges)
  const variables = useEditorStore((state) => state.variables)
  const loadGraph = useEditorStore((state) => state.loadGraph)
  const setNodeStatus = useEditorStore((state) => state.setNodeStatus)
  const resetRunStatuses = useEditorStore((state) => state.resetRunStatuses)
  const appendLog = useEditorStore((state) => state.appendLog)

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
    Array<ApprovalRequest & { nodeId: string }>
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

  const graphJson = JSON.stringify(serializeGraph(nodes, edges, variables), null, 2)

  async function compileAndRun(shouldRecord = false) {
    const order = DEMO_ORDERS.find((item) => item.order_id === selectedOrderId)
    const inputs: RunInputs | undefined = order
      ? { order_id: order.order_id, reason: order.reason, amount: order.amount }
      : undefined
    setRunning(true)
    setRunError(null)
    setCompileResult(null)
    setRunResult(null)
    setPendingApprovals([])
    setApprovalError(null)
    resetRunStatuses()
    const collected: RunEvent[] = []
    try {
      const saved = await saveGraph(serializeGraph(nodes, edges, variables))
      appendLog(`已保存 Graph：${saved.id}`)
      const compiled = await compileGraph(saved.id)
      setCompileResult(compiled)
      appendLog(`编译成功：入口 ${compiled.entrypoints.join(', ')}`)
      const executed = await streamRun(saved.id, inputs, (event) => {
        collected.push(event)
        if (event.type === 'node_start') {
          setNodeStatus(event.node_id, 'running')
          appendLog(`▶ 节点开始：${event.node_id}`)
          if (event.approval) {
            const approval = event.approval
            setPendingApprovals((items) => [...items, { ...approval, nodeId: event.node_id }])
            appendLog(`⏸ ${event.node_id} 等待人工审批：${approval.summary}`)
          }
        } else if (event.type === 'node_end') {
          setNodeStatus(event.node_id, 'completed')
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
      })
      const toolOutputs = Object.values(executed.outputs).filter(
        (output): output is { result?: { status?: string } } =>
          typeof output === 'object' && output !== null && 'result' in output,
      )
      const finalStatus = toolOutputs.find((output) => output.result?.status)?.result?.status
      appendLog(`运行结束：${finalStatus ?? executed.status}`)
      setRunResult(executed)
      setRunOpen(true)
      if (shouldRecord) {
        const name = caseName.trim() || `录制 ${saved.id} ${new Date().toLocaleString()}`
        const savedCase = await saveRecording({
          name,
          graph_id: saved.id,
          inputs: inputs ?? null,
          steps: toSteps(collected),
          status: executed.status,
        })
        appendLog(`已保存录制用例：${savedCase.id}（${savedCase.steps.length} 个步骤）`)
        setRecordings(await listRecordings())
        setCaseName(`录制 ${new Date().toLocaleString()}`)
      }
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setRunError(message)
      if (shouldRecord) setRecordingError(message)
      appendLog(`✗ 运行失败：${message}`)
    } finally {
      setRunning(false)
      setRecordBusy(false)
    }
  }

  async function generateDraft() {
    setNlLoading(true)
    setNlError(null)
    try {
      const { graph } = await nlGenerate(nlPrompt)
      loadGraph(graph)
      setNlOpen(false)
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
      const report = await replayRecording(caseId)
      setReports((prev) => ({ ...prev, [caseId]: report }))
    } catch (error) {
      setRecordingError(error instanceof Error ? error.message : String(error))
    } finally {
      setReplayBusyId(null)
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

  function dismissCurrentApproval() {
    if (!currentApproval) return
    setApprovalError(null)
    setPendingApprovals((items) => items.filter((item) => item.token !== currentApproval.token))
  }

  return (
    <Layout className="editor-layout">
      <Header className="editor-header">
        <Typography.Title level={3} style={{ margin: 0 }}>
          Atlas 流程编辑器
        </Typography.Title>
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
          <Button onClick={() => setNlOpen(true)}>自然语言生成</Button>
          <Button onClick={openTemplateBrowser}>从模板新建</Button>
          <Button onClick={openRecordings}>录制与回放</Button>
          <Button onClick={() => setExportOpen(true)}>导出 Graph JSON</Button>
          <FeedbackButton />
          <Button type="primary" loading={running} onClick={() => compileAndRun()}>
            编译并运行
          </Button>
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
                border: '1px solid var(--color-border, #d9d9d9)',
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
                  border: '1px solid var(--color-border, #d9d9d9)',
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
                {report && (
                  <div style={{ marginTop: 8 }}>
                    <Space size={8} wrap>
                      <Tag color={report.matches ? 'green' : 'red'}>
                        {report.matches ? '匹配' : '不匹配'}
                      </Tag>
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
        footer={[
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
        ]}
      >
        {currentApproval && (
          <Space orientation="vertical" size={8} style={{ width: '100%' }}>
            <div>
              <Typography.Text type="secondary">节点</Typography.Text>
              <div>{currentApproval.nodeId}</div>
            </div>
            <div>
              <Typography.Text type="secondary">审批说明</Typography.Text>
              <div>{currentApproval.summary}</div>
            </div>
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
    </Layout>
  )
}
