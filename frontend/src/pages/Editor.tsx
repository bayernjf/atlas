import { Layout, Typography } from 'antd'
import { FlowCanvas } from '../components/canvas/FlowCanvas'
import { NodePanel } from '../components/nodePanel/NodePanel'
import { PropertyPanel } from '../components/propertyPanel/PropertyPanel'
import { DebugConsole } from '../components/debugConsole/DebugConsole'

const { Header, Sider, Content, Footer } = Layout

export function Editor() {
  return (
    <Layout className="editor-layout">
      <Header className="editor-header">
        <Typography.Title level={3}>Atlas 流程编辑器</Typography.Title>
      </Header>
      <Layout>
        <Sider width={260} theme="light" className="editor-sider">
          <NodePanel />
        </Sider>
        <Content className="editor-content">
          <FlowCanvas />
        </Content>
        <Sider width={300} theme="light" className="editor-sider">
          <PropertyPanel />
        </Sider>
      </Layout>
      <Footer className="editor-footer">
        <DebugConsole />
      </Footer>
    </Layout>
  )
}
