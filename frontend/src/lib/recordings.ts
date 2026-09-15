/**
 * 操作录制纯逻辑：把一次 /run/stream 的事件流组装为录制步骤（04 §5.11）。
 * node_id 重复时保末（parallel 两次 node_end、loop 重访），位置以末次出现为准。
 */
import type { RecordStep, RunEvent } from './apiClient'

export function toSteps(events: RunEvent[]): RecordStep[] {
  const byId = new Map<string, RecordStep>()
  const order: string[] = []
  for (const event of events) {
    if (event.type !== 'node_end') continue
    const output =
      event.output && typeof event.output === 'object'
        ? (event.output as Record<string, unknown>)
        : {}
    if (byId.has(event.node_id)) {
      order.splice(order.indexOf(event.node_id), 1)
    }
    byId.set(event.node_id, { node_id: event.node_id, node_type: event.node_type, output })
    order.push(event.node_id)
  }
  return order.map((id) => byId.get(id) as RecordStep)
}
