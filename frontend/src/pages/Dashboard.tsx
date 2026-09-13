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
            <Typography.Title level={4}>W1-W2 基础骨架</Typography.Title>
            <Typography.Paragraph>
              前端框架已接入 React Flow、Zustand 与 Ant Design。当前可预览编辑器布局、拖拽节点并编辑节点名称。
            </Typography.Paragraph>
            <Button type="primary" onClick={onOpenEditor}>
              打开流程编辑器
            </Button>
          </Card>
          <Row gutter={16}>
            <Col span={8}>
              <Card title="Graph"><Typography.Text>可执行因果图骨架</Typography.Text></Card>
            </Col>
            <Col span={8}>
              <Card title="Loop"><Typography.Text>OODA 最小循环已跑通</Typography.Text></Card>
            </Col>
            <Col span={8}>
              <Card title="Harness"><Typography.Text>能力接入层待 W3-W4 实现</Typography.Text></Card>
            </Col>
          </Row>
        </Space>
      </Content>
    </Layout>
  )
}
