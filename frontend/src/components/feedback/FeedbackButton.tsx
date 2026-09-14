import { useState } from 'react'
import { Alert, Button, Input, Modal, Radio, Typography } from 'antd'
import { submitFeedback, type FeedbackType } from '../../lib/apiClient'

const { TextArea } = Input

export function FeedbackButton() {
  const [open, setOpen] = useState(false)
  const [type, setType] = useState<FeedbackType>('bug')
  const [content, setContent] = useState('')
  const [contact, setContact] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  function close() {
    setOpen(false)
    window.setTimeout(() => {
      setType('bug')
      setContent('')
      setContact('')
      setError(null)
      setDone(false)
    }, 200)
  }

  async function submit() {
    if (!content.trim()) {
      setError('请填写反馈内容')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      await submitFeedback({ type, content: content.trim(), contact: contact.trim() })
      setDone(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <>
      <Button onClick={() => setOpen(true)}>反馈</Button>
      <Modal
        title="提交试用反馈"
        open={open}
        onCancel={close}
        onOk={done ? close : submit}
        confirmLoading={submitting}
        okText={done ? '关闭' : '提交反馈'}
        cancelText={done ? undefined : '取消'}
        cancelButtonProps={{ style: done ? { display: 'none' } : undefined }}
      >
        {done ? (
          <Alert
            type="success"
            showIcon
            message="反馈已收到，谢谢！"
            description="你的反馈会直接决定我们下一步做什么。可以继续试用，也欢迎再提一条。"
          />
        ) : (
          <>
            <Typography.Paragraph>
              遇到的问题或建议，一句话也行。告诉我们你当时想做什么、发生了什么。
            </Typography.Paragraph>
            <Radio.Group
              value={type}
              onChange={(event) => setType(event.target.value as FeedbackType)}
              options={[
                { value: 'bug', label: '问题 / Bug' },
                { value: 'suggestion', label: '建议' },
              ]}
              optionType="button"
              buttonStyle="solid"
              style={{ marginBottom: 12 }}
            />
            <TextArea
              rows={4}
              placeholder="例如：选了 12345 运行后，画布上的节点没有反应……"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              maxLength={2000}
              showCount
            />
            <Input
              style={{ marginTop: 12 }}
              placeholder="联系方式（选填，方便我们追问细节）"
              value={contact}
              onChange={(event) => setContact(event.target.value)}
              maxLength={200}
            />
            {error && <Alert type="error" showIcon message={error} style={{ marginTop: 12 }} />}
          </>
        )}
      </Modal>
    </>
  )
}
