import { Card, Tag, Typography } from 'antd'
import { NODE_CATALOG, NODE_KINDS, type NodeKind } from '../../lib/nodeCatalog'

const DND_MIME = 'application/atlas-node'

export function NodePanel() {
  const onDragStart = (event: React.DragEvent, kind: NodeKind) => {
    event.dataTransfer.setData(DND_MIME, kind)
    event.dataTransfer.effectAllowed = 'move'
  }

  return (
    <Card className="side-card" title="节点面板（拖入画布）" size="small">
      {NODE_KINDS.map((kind) => {
        const meta = NODE_CATALOG[kind]
        return (
          <div
            key={kind}
            className="node-palette-item"
            draggable
            onDragStart={(event) => onDragStart(event, kind)}
            style={{ borderLeftColor: meta.color }}
          >
            <Tag color={meta.color} style={{ marginInlineEnd: 6 }}>
              {meta.label}
            </Tag>
            <Typography.Text type="secondary">{meta.description}</Typography.Text>
          </div>
        )
      })}
    </Card>
  )
}
