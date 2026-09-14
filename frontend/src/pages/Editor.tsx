import { useState } from 'react'
import { Alert, Button, Layout, Modal, Select, Space, Tabs, Typography, Input } from 'antd'
import { FlowCanvas } from '../components/canvas/FlowCanvas'
import { NodePanel } from '../components/nodePanel/NodePanel'
import { VariablesPanel } from '../components/variablePanel/VariablesPanel'
import { PropertyPanel } from '../components/propertyPanel/PropertyPanel'
import { DebugConsole } from '../components/debugConsole/DebugConsole'
import { FeedbackButton } from '../components/feedback/FeedbackButton'
import { useEditorStore } from '../store/editorStore'
import { serializeGraph } from '../lib/graphSerializer'
import {
  compileGraph,
  nlGenerate,
  saveGraph,
  streamRun,
  type CompileResult,
  type RunInputs,
  type RunResult,
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
  const [running, setRunning] = useState(false)
  const [nlLoading, setNlLoading] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [nlError, setNlError] = useState<string | null>(null)
  const [compileResult, setCompileResult] = useState<CompileResult | null>(null)
  const [runResult, setRunResult] = useState<RunResult | null>(null)
  const [selectedOrderId, setSelectedOrderId] = useState('12345')
  const [nlPrompt, setNlPrompt] = useState('帮我做一个电商退款自动审批流程')

  const graphJson = JSON.stringify(serializeGraph(nodes, edges, variables), null, 2)

  async function compileAndRun() {
    const order = DEMO_ORDERS.find((item) => item.order_id === selectedOrderId)
    const inputs: RunInputs | undefined = order
      ? { order_id: order.order_id, reason: order.reason, amount: order.amount }
      : undefined
    setRunning(true)
    setRunError(null)
    setCompileResult(null)
    setRunResult(null)
    resetRunStatuses()
    try {
      const saved = await saveGraph(serializeGraph(nodes, edges, variables))
      appendLog(`已保存 Graph：${saved.id}`)
      const compiled = await compileGraph(saved.id)
      setCompileResult(compiled)
      appendLog(`编译成功：入口 ${compiled.entrypoints.join(', ')}`)
      const executed = await streamRun(saved.id, inputs, (event) => {
        if (event.type === 'node_start') {
          setNodeStatus(event.node_id, 'running')
          appendLog(`▶ 节点开始：${event.node_id}`)
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
            branches?: Array<{ label: string; target: string; status: string; error: string }>
            result?: unknown
          }
          const decision = output?.decision
          if (decision?.action) {
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
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setRunError(message)
      appendLog(`✗ 运行失败：${message}`)
    } finally {
      setRunning(false)
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
          <Button onClick={() => setExportOpen(true)}>导出 Graph JSON</Button>
          <FeedbackButton />
          <Button type="primary" loading={running} onClick={compileAndRun}>
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
