import { Button, Card, Col, Layout, Row, Space, Typography } from 'antd'
import { UserBadge } from '../components/UserBadge'
import { roleCan, type Principal } from '../lib/auth'
import { useTranslation } from '../locales'

const { Content, Header } = Layout

type DashboardProps = {
  principal: Principal
  onLogout: () => void
  onOpenEditor: () => void
  onOpenMonitoring: () => void
  onOpenMemory: () => void
  onOpenConnections: () => void
  onOpenUsers: () => void
  onOpenApprovals: () => void
  onOpenAudit: () => void
  onOpenOpenApi: () => void
  onOpenWaits: () => void
  onOpenSchedules: () => void
  onOpenAgents: () => void
  onOpenReflection: () => void
  onOpenModels: () => void
  onOpenUsage: () => void
  onOpenTemplates: () => void
  onOpenEmbeds: () => void
  onOpenAdapterDrafts: () => void
  onOpenExperiments: () => void
}

export function Dashboard({ principal, onLogout, onOpenEditor, onOpenMonitoring, onOpenMemory, onOpenConnections, onOpenUsers, onOpenApprovals, onOpenAudit, onOpenOpenApi, onOpenWaits, onOpenSchedules, onOpenAgents, onOpenReflection, onOpenModels, onOpenUsage, onOpenTemplates, onOpenEmbeds, onOpenAdapterDrafts, onOpenExperiments }: DashboardProps) {
  // 页面专属文案走 dashboard namespace；品牌名/主导航是跨页通用文案，显式取 common: 前缀。
  const { t } = useTranslation('dashboard')
  // 导航卡是 div onClick（antd Card），补键盘语义：Enter/Space 激活（a11y 0→1 基础）。
  const activate = (open: () => void) => (event: React.KeyboardEvent) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      open()
    }
  }
  return (
    <Layout className="page-layout">
      <Header className="page-header" style={{ justifyContent: 'space-between' }}>
        <Typography.Title level={3}>{t('common:brand.appName')}</Typography.Title>
        <UserBadge principal={principal} onLogout={onLogout} />
      </Header>
      <Content className="page-content">
        <Space orientation="vertical" size="large" style={{ width: '100%' }}>
          <Card>
            <Typography.Title level={4}>{t('demo.title')}</Typography.Title>
            <Typography.Paragraph>{t('demo.description')}</Typography.Paragraph>
            <Space wrap>
              <Button type="primary" onClick={onOpenEditor}>
                {t('common:nav.openEditor')}
              </Button>
              <Button onClick={onOpenMonitoring}>{t('common:nav.monitoring')}</Button>
              <Button onClick={onOpenApprovals}>{t('common:nav.approvals')}</Button>
              {roleCan(principal.role, 'operate') && (
                <Button onClick={onOpenWaits}>{t('common:nav.waits')}</Button>
              )}
              <Button onClick={onOpenMemory}>{t('common:nav.memory')}</Button>
              <Button onClick={onOpenSchedules}>{t('common:nav.schedules')}</Button>
              <Button onClick={onOpenAgents}>{t('common:nav.agents')}</Button>
              <Button onClick={onOpenReflection}>{t('reflection:menu.reflection')}</Button>
              <Button onClick={onOpenModels}>{t('common:nav.models')}</Button>
              <Button onClick={onOpenUsage}>{t('common:nav.usage')}</Button>
              <Button onClick={onOpenTemplates}>{t('common:nav.templates')}</Button>
              {roleCan(principal.role, 'operate') && (
                <Button onClick={onOpenConnections}>{t('common:nav.connections')}</Button>
              )}
              {roleCan(principal.role, 'operate') && (
                <Button onClick={onOpenOpenApi}>{t('common:nav.openapi')}</Button>
              )}
              {roleCan(principal.role, 'administer') && (
                <Button onClick={onOpenUsers}>{t('common:nav.users')}</Button>
              )}
              {roleCan(principal.role, 'administer') && (
                <Button onClick={onOpenEmbeds}>{t('common:nav.embeds')}</Button>
              )}
              <Button onClick={onOpenAdapterDrafts}>{t('common:nav.adapterDrafts')}</Button>
              <Button onClick={onOpenExperiments}>{t('common:nav.experiments')}</Button>
              {roleCan(principal.role, 'administer') && (
                <Button onClick={onOpenAudit}>{t('common:nav.audit')}</Button>
              )}
            </Space>
          </Card>
          <Row gutter={16}>
            <Col xs={24} md={8}>
              <Card
                title="Graph"
                hoverable
                onClick={onOpenEditor}
                role="button"
                tabIndex={0}
                onKeyDown={activate(onOpenEditor)}
                extra={<Typography.Text type="secondary">{t('demo.cards.enter')}</Typography.Text>}
              >
                <Typography.Text>{t('demo.cards.graph')}</Typography.Text>
              </Card>
            </Col>
            <Col xs={24} md={8}>
              <Card
                title="Loop"
                hoverable
                onClick={onOpenReflection}
                role="button"
                tabIndex={0}
                onKeyDown={activate(onOpenReflection)}
                extra={<Typography.Text type="secondary">{t('demo.cards.enter')}</Typography.Text>}
              >
                <Typography.Text>{t('demo.cards.loop')}</Typography.Text>
              </Card>
            </Col>
            <Col xs={24} md={8}>
              <Card
                title="Harness"
                hoverable
                onClick={onOpenOpenApi}
                role="button"
                tabIndex={0}
                onKeyDown={activate(onOpenOpenApi)}
                extra={<Typography.Text type="secondary">{t('demo.cards.enter')}</Typography.Text>}
              >
                <Typography.Text>{t('demo.cards.harness')}</Typography.Text>
              </Card>
            </Col>
          </Row>
        </Space>
      </Content>
    </Layout>
  )
}
