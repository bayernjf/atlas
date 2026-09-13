import { Button, Card, Space, Tag, Typography } from 'antd'
import type { NodeKind } from '../../store/editorStore'
import { useEditorStore } from '../../store/editorStore'

const nodeTypes: Array<{ kind: NodeKind; label: string; description: string }> = [
  { kind: 'trigger', label: '触发器', description: '流程入口与事件来源' },
  { kind: 'decision', label: 'AI 决策', description: '基于上下文判断下一步' },
  { kind: 'action', label: '工具调用', description: '执行外部平台操作' },
]

export function NodePanel() {
  const addNode = useEditorStore((state) => state.addNode)

  return (
    <Card className="side-card" title="节点面板" size="small">
      <Space orientation="vertical" style={{ width: '100%' }}>
        {nodeTypes.map((nodeType) => (
          <div key={nodeType.kind} className="node-type-row">
            <div>
              <Tag color="blue">{nodeType.label}</Tag>
              <Typography.Text type="secondary">{nodeType.description}</Typography.Text>
            </div>
            <Button size="small" onClick={() => addNode(nodeType.kind)}>
              添加
            </Button>
          </div>
        ))}
      </Space>
    </Card>
  )
}
