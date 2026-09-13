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
            <Typography.Title level={4}>W7-W8 编译与运行</Typography.Title>
            <Typography.Paragraph>
              编辑器 Graph JSON 可一键保存到后端、编译为 LangGraph 并运行，查看拓扑与节点产出。
            </Typography.Paragraph>
            <Button type="primary" onClick={onOpenEditor}>
              打开流程编辑器
            </Button>
          </Card>
          <Row gutter={16}>
            <Col span={8}>
              <Card title="Graph"><Typography.Text>DSL → LangGraph 编译与运行已打通（W7-W8 占位执行器）</Typography.Text></Card>
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
