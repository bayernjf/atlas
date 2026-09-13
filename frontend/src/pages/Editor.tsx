import { useState } from 'react'
import { Button, Layout, Modal, Tabs, Typography } from 'antd'
import { FlowCanvas } from '../components/canvas/FlowCanvas'
import { NodePanel } from '../components/nodePanel/NodePanel'
import { VariablesPanel } from '../components/variablePanel/VariablesPanel'
import { PropertyPanel } from '../components/propertyPanel/PropertyPanel'
import { DebugConsole } from '../components/debugConsole/DebugConsole'
import { useEditorStore } from '../store/editorStore'
import { serializeGraph } from '../lib/graphSerializer'

const { Header, Sider, Content, Footer } = Layout

export function Editor() {
  const nodes = useEditorStore((state) => state.nodes)
  const edges = useEditorStore((state) => state.edges)
  const variables = useEditorStore((state) => state.variables)
  const [exportOpen, setExportOpen] = useState(false)

  const graphJson = JSON.stringify(serializeGraph(nodes, edges, variables), null, 2)

  return (
    <Layout className="editor-layout">
      <Header className="editor-header">
        <Typography.Title level={3} style={{ margin: 0 }}>
          Atlas 流程编辑器
        </Typography.Title>
        <Button onClick={() => setExportOpen(true)}>导出 Graph JSON</Button>
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
    </Layout>
  )
}
