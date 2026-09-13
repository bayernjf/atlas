import { Button, Card, Col, Layout, Row, Space, Typography } from 'antd'

const { Content, Header } = Layout

type DashboardProps = {
  onOpenEditor: () => void
}

export function Dashboard({ onOpenEditor }: DashboardProps) {
  return (
    <Layout className="page-layout">
      <Header className="page-header">
        <Typography.Title level={3}>Atlas 运营体编排平台</Typography.Title>
      </Header>
      <Content className="page-content">
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          <Card>
            <Typography.Title level={4}>W5-W6 编辑器核心</Typography.Title>
            <Typography.Paragraph>
              支持拖拽新增节点、触发器 / AI 决策 / 工具调用三类节点配置、实时校验、全局变量与 {'{{路径}}'} 引用，并可导出 Graph JSON。
            </Typography.Paragraph>
            <Button type="primary" onClick={onOpenEditor}>
              打开流程编辑器
            </Button>
          </Card>
          <Row gutter={16}>
            <Col span={8}>
              <Card title="Graph"><Typography.Text>可执行因果图骨架，编辑器可导出 Graph JSON</Typography.Text></Card>
            </Col>
            <Col span={8}>
              <Card title="Loop"><Typography.Text>OODA 最小循环已跑通</Typography.Text></Card>
            </Col>
            <Col span={8}>
              <Card title="Harness"><Typography.Text>契约/注册层 + Playwright Web 适配器（三层定位）已跑通</Typography.Text></Card>
            </Col>
          </Row>
        </Space>
      </Content>
    </Layout>
  )
}
