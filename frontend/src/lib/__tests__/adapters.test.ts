import { describe, expect, it } from 'vitest'
import { buildToolOptions } from '../adapters'

const adapters = [
  {
    id: 'shop',
    type: 'shop',
    healthy: true,
    tools: [
      { name: 'login', description: '登录', permission: 'write', idempotent: false },
      { name: 'list_pending_refunds', description: '列表', permission: 'read', idempotent: true },
    ],
  },
  {
    id: 'http',
    type: 'api',
    healthy: true,
    tools: [{ name: 'request', description: 'HTTP', permission: 'write', idempotent: false }],
  },
  {
    id: 'down',
    type: 'api',
    healthy: false,
    tools: [{ name: 'ping', description: '', permission: 'read', idempotent: true }],
  },
]

describe('buildToolOptions', () => {
  it('按适配器分组并使用复合 value', () => {
    const groups = buildToolOptions(adapters)
    expect(groups.map((group) => group.label)).toEqual(['shop（shop）', 'http（api）'])
    expect(groups[1].options).toEqual([
      { value: 'http/request', label: 'http/request（write）' },
    ])
  })

  it('标记权限与幂等', () => {
    const [shopGroup] = buildToolOptions(adapters)
    expect(shopGroup.options).toEqual([
      { value: 'shop/login', label: 'shop/login（write）' },
      { value: 'shop/list_pending_refunds', label: 'shop/list_pending_refunds（read · 幂等）' },
    ])
  })

  it('过滤 unhealthy 适配器', () => {
    const values = buildToolOptions(adapters).flatMap((group) => group.options.map((o) => o.value))
    expect(values).not.toContain('down/ping')
  })
})
