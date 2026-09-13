import { Card, Tag, Typography } from 'antd'
import { useEditorStore } from '../../store/editorStore'

export function DebugConsole() {
  const logs = useEditorStore((state) => state.logs)

  return (
    <Card
      className="debug-card"
      title={
        <div className="debug-title">
          <span>调试控制台</span>
          <Tag color="green">{logs.length} 条事件</Tag>
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
