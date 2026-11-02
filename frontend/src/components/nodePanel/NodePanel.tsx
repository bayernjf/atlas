import { Card, Tag, Typography } from 'antd'
import { NODE_CATALOG, NODE_KINDS, type NodeKind } from '../../lib/nodeCatalog'
import { useTranslation } from '../../locales'

const DND_MIME = 'application/atlas-node'

export function NodePanel() {
  const { t } = useTranslation('editor')
  const onDragStart = (event: React.DragEvent, kind: NodeKind) => {
    event.dataTransfer.setData(DND_MIME, kind)
    event.dataTransfer.effectAllowed = 'move'
  }

  return (
    <Card className="side-card" title={t('nodePanel.title')} size="small">
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
            {/* 节点色只保留在左边框色条：AntD Tag 实底 hex（*6 色板）配白字实测
                1.77–3.54:1，不达 WCAG AA（4.5:1）；默认灰 Tag 才达标。 */}
            <Tag style={{ marginInlineEnd: 6 }}>
              {t(`nodeCatalog.${kind}.label`)}
            </Tag>
            <Typography.Text type="secondary">{t(`nodeCatalog.${kind}.description`)}</Typography.Text>
          </div>
        )
      })}
    </Card>
  )
}
