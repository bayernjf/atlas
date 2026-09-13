import { useState } from 'react'
import { Alert, Button, Layout, Modal, Tabs, Typography } from 'antd'
import { FlowCanvas } from '../components/canvas/FlowCanvas'
import { NodePanel } from '../components/nodePanel/NodePanel'
import { VariablesPanel } from '../components/variablePanel/VariablesPanel'
import { PropertyPanel } from '../components/propertyPanel/PropertyPanel'
import { DebugConsole } from '../components/debugConsole/DebugConsole'
import { useEditorStore } from '../store/editorStore'
import { serializeGraph } from '../lib/graphSerializer'
import { compileGraph, runGraph, saveGraph, type CompileResult, type RunResult } from '../lib/apiClient'

const { Header, Sider, Content, Footer } = Layout

export function Editor() {
  const nodes = useEditorStore((state) => state.nodes)
  const edges = useEditorStore((state) => state.edges)
  const variables = useEditorStore((state) => state.variables)
  const [exportOpen, setExportOpen] = useState(false)
  const [runOpen, setRunOpen] = useState(false)
  const [running, setRunning] = useState(false)
  const [runError, setRunError] = useState<string | null>(null)
  const [compileResult, setCompileResult] = useState<CompileResult | null>(null)
  const [runResult, setRunResult] = useState<RunResult | null>(null)

  const graphJson = JSON.stringify(serializeGraph(nodes, edges, variables), null, 2)

  async function compileAndRun() {
    setRunning(true)
    setRunError(null)
    setCompileResult(null)
    setRunResult(null)
    try {
      const saved = await saveGraph(serializeGraph(nodes, edges, variables))
      const compiled = await compileGraph(saved.id)
      const executed = await runGraph(saved.id)
      setCompileResult(compiled)
      setRunResult(executed)
      setRunOpen(true)
    } catch (error) {
      setRunError(error instanceof Error ? error.message : String(error))
    } finally {
      setRunning(false)
    }
  }

  return (
    <Layout className="editor-layout">
      <Header className="editor-header">
        <Typography.Title level={3} style={{ margin: 0 }}>
          Atlas 流程编辑器
        </Typography.Title>
        <div>
          <Button onClick={() => setExportOpen(true)} style={{ marginRight: 8 }}>
            导出 Graph JSON
          </Button>
          <Button type="primary" loading={running} onClick={compileAndRun}>
            编译并运行
          </Button>
        </div>
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
