import type { AdapterInfo } from './apiClient'

export type ToolOptionGroup = {
  label: string
  options: Array<{ value: string; label: string }>
}

/**
 * GET /api/adapters → antd Select 分组选项（04 §4.4 注册发现）。
 * 仅保留 healthy 适配器；value 为 `${adapter_id}/${tool}` 复合串，与 config.tool 契约一致。
 */
export function buildToolOptions(adapters: AdapterInfo[]): ToolOptionGroup[] {
  return adapters
    .filter((adapter) => adapter.healthy)
    .map((adapter) => ({
      label: `${adapter.id}（${adapter.type}）`,
      options: adapter.tools.map((tool) => {
        const marks = [tool.permission, tool.idempotent ? '幂等' : null].filter(Boolean).join(' · ')
        return {
          value: `${adapter.id}/${tool.name}`,
          label: marks ? `${adapter.id}/${tool.name}（${marks}）` : `${adapter.id}/${tool.name}`,
        }
      }),
    }))
}
