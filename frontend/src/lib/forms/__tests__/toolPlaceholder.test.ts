import { describe, expect, it } from 'vitest'
import { toolParamsPlaceholder } from '../toolPlaceholder'

describe('toolParamsPlaceholder', () => {
  it('maps known demo tools to their example params', () => {
    expect(toolParamsPlaceholder('http/request')).toContain('"method":"GET"')
    expect(toolParamsPlaceholder('http/mock')).toContain('"method":"GET"')
    expect(toolParamsPlaceholder('database/query')).toContain('SELECT order_id')
    expect(toolParamsPlaceholder('database/execute')).toContain('UPDATE orders')
  })

  it('message/send shows an IM robot example with a single URL and secret', () => {
    const placeholder = toolParamsPlaceholder('message/send')
    expect(placeholder).toContain('"channel":"dingtalk"')
    expect(placeholder).toContain('"to":"https://oapi.dingtalk.com/robot/send?access_token=xxx"')
    expect(placeholder).toContain('"secret":"SEC可选"')
  })

  it('falls back to a generic placeholder for unknown tools', () => {
    expect(toolParamsPlaceholder(undefined)).toBe('{"element_desc": "提交按钮"}')
    expect(toolParamsPlaceholder('custom/tool')).toBe('{"element_desc": "提交按钮"}')
  })
})
