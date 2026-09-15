import { Button, Card, Col, Layout, Row, Space, Typography } from 'antd'
import { UserBadge } from '../components/UserBadge'
import type { Principal } from '../lib/auth'

const { Content, Header } = Layout

type DashboardProps = {
  principal: Principal
  onLogout: () => void
  onOpenEditor: () => void
  onOpenMonitoring: () => void
}

export function Dashboard({ principal, onLogout, onOpenEditor, onOpenMonitoring }: DashboardProps) {
  return (
    <Layout className="page-layout">
      <Header className="page-header" style={{ justifyContent: 'space-between' }}>
        <Typography.Title level={3}>Atlas 运营体编排平台</Typography.Title>
        <UserBadge principal={principal} onLogout={onLogout} />
      </Header>
      <Content className="page-content">
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          <Card>
            <Typography.Title level={4}>W9-W10 端到端 Demo：电商退款自动化</Typography.Title>
            <Typography.Paragraph>
              选择退款单一键运行：AI 按退款原因与审批限额决策自动退款或转人工，画布实时显示节点进度；
              也可用自然语言生成退款流程草稿。
            </Typography.Paragraph>
            <Space>
              <Button type="primary" onClick={onOpenEditor}>
                打开流程编辑器
              </Button>
              <Button onClick={onOpenMonitoring}>监控告警</Button>
            </Space>
          </Card>
          <Row gutter={16}>
            <Col span={8}>
              <Card title="Graph"><Typography.Text>退款流程经 DSL → LangGraph 编译运行，节点事件 SSE 实时上屏</Typography.Text></Card>
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
