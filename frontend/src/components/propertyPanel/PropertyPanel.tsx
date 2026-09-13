import { Card, Empty, Input, Space, Tag, Typography } from 'antd'
import { useEditorStore } from '../../store/editorStore'

const kindLabel = {
  trigger: '触发器',
  decision: 'AI 决策',
  action: '工具调用',
}

export function PropertyPanel() {
  const nodes = useEditorStore((state) => state.nodes)
  const selectedNodeId = useEditorStore((state) => state.selectedNodeId)
  const updateSelectedLabel = useEditorStore((state) => state.updateSelectedLabel)
  const selectedNode = nodes.find((node) => node.id === selectedNodeId)

  return (
    <Card className="side-card" title="属性面板" size="small">
      {selectedNode ? (
        <Space orientation="vertical" style={{ width: '100%' }}>
          <div>
            <Typography.Text type="secondary">节点 ID</Typography.Text>
            <div>{selectedNode.id}</div>
          </div>
          <div>
            <Typography.Text type="secondary">类型</Typography.Text>
            <div>
              <Tag color="geekblue">{kindLabel[selectedNode.data.kind]}</Tag>
            </div>
          </div>
          <label>
            <Typography.Text type="secondary">节点名称</Typography.Text>
            <Input
              value={selectedNode.data.label}
              onChange={(event) => updateSelectedLabel(event.target.value)}
            />
          </label>
        </Space>
      ) : (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="选择画布中的节点查看属性" />
      )}
    </Card>
  )
}
