import { Card, Tag, Typography } from 'antd'
import { useEditorStore } from '../../store/editorStore'
import { useTranslation } from '../../locales'

export function DebugConsole() {
  const logs = useEditorStore((state) => state.logs)
  const { t } = useTranslation('editor')

  return (
    <Card
      className="debug-card"
      title={
        <div className="debug-title">
          <span>{t('debugConsole.title')}</span>
          <Tag color="green">{t('debugConsole.eventCount', { count: logs.length })}</Tag>
        </div>
      }
      size="small"
    >
      <div className="debug-log-list">
        {logs.map((log, index) => (
          <Typography.Text code key={`${index}-${log}`}>
            {log}
          </Typography.Text>
        ))}
      </div>
    </Card>
  )
}
