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
  onOpenReflection: () => void
  onOpenModels: () => void
  onOpenTemplates: () => void
}

export function Dashboard({ principal, onLogout, onOpenEditor, onOpenMonitoring, onOpenMemory, onOpenConnections, onOpenUsers, onOpenApprovals, onOpenAudit, onOpenOpenApi, onOpenWaits, onOpenSchedules, onOpenReflection, onOpenModels, onOpenTemplates }: DashboardProps) {
  // 页面专属文案走 dashboard namespace；品牌名/主导航是跨页通用文案，显式取 common: 前缀。
  const { t } = useTranslation('dashboard')
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
              <Button onClick={onOpenReflection}>{t('reflection:menu.reflection')}</Button>
              <Button onClick={onOpenModels}>{t('common:nav.models')}</Button>
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
                <Button onClick={onOpenAudit}>{t('common:nav.audit')}</Button>
              )}
            </Space>
          </Card>
          <Row gutter={16}>
            <Col span={8}>
              <Card
                title="Graph"
                hoverable
                onClick={onOpenEditor}
                extra={<Typography.Text type="secondary">{t('demo.cards.enter')}</Typography.Text>}
              >
                <Typography.Text>{t('demo.cards.graph')}</Typography.Text>
              </Card>
            </Col>
            <Col span={8}>
              <Card
                title="Loop"
                hoverable
                onClick={onOpenReflection}
                extra={<Typography.Text type="secondary">{t('demo.cards.enter')}</Typography.Text>}
              >
                <Typography.Text>{t('demo.cards.loop')}</Typography.Text>
              </Card>
            </Col>
            <Col span={8}>
              <Card
                title="Harness"
                hoverable
                onClick={onOpenOpenApi}
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
