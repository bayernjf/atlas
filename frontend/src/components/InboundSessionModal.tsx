import { useEffect, useState } from 'react'
import { App, Empty, Modal, Space, Spin, Tag, Typography, theme } from 'antd'
import { ApiError, getInboundSession, type InboundSessionMessage } from '../lib/apiClient'
import { useTranslation } from '../locales'

type Props = {
  conversationId: string | null
  onClose: () => void
}

// 打包 BO（docs/131 D-D7）：假供应商会话视图——进程内回写记录（user 消息＋关联 runId）。
export function InboundSessionModal({ conversationId, onClose }: Props) {
  const { t } = useTranslation('channels')
  const { message } = App.useApp()
  const { token } = theme.useToken()
  const [items, setItems] = useState<InboundSessionMessage[] | null>(null)

  useEffect(() => {
    if (!conversationId) return
    getInboundSession(conversationId)
      .then(setItems)
      .catch((err: unknown) => {
        message.error(err instanceof ApiError ? err.message : t('inbound.sessionView.loadError'))
        onClose()
      })
  }, [conversationId, message, onClose, t])

  return (
    <Modal
      title={`${t('inbound.sessionView.title')} · ${conversationId ?? ''}`}
      open={conversationId !== null}
      onCancel={onClose}
      footer={null}
      width={560}
      destroyOnHidden
    >
      {items === null ? (
        <Spin />
      ) : items.length === 0 ? (
        <Empty description={t('inbound.sessionView.empty')} />
      ) : (
        <Space direction="vertical" size={10} style={{ width: '100%' }}>
          {items.map((item, index) => (
            <div
              key={`${item.messageId}-${index}`}
              style={{
                border: `1px solid ${token.colorBorderSecondary}`,
                borderRadius: 8,
                padding: '8px 12px',
              }}
            >
              <Space size={8} wrap>
                <Tag color="blue">{t('inbound.sessionView.roleUser')}</Tag>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {item.createdAt}
                </Typography.Text>
                {item.runId && (
                  <Typography.Text code style={{ fontSize: 12 }}>
                    {item.runId}
                  </Typography.Text>
                )}
              </Space>
              <Typography.Paragraph style={{ marginBottom: 0, marginTop: 4 }}>
                {typeof item.data?.text === 'string' ? item.data.text : JSON.stringify(item.data)}
              </Typography.Paragraph>
            </div>
          ))}
        </Space>
      )}
    </Modal>
  )
}
