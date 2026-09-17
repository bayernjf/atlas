import { describe, expect, it } from 'vitest'
import { WidgetRegistry } from '../registry'
import { BUILTIN_WIDGETS, type WidgetComponent } from '../types'

const stub: WidgetComponent = () => null

describe('WidgetRegistry（U39①）', () => {
  it('register/has/get/names 行为正确', () => {
    const registry = new WidgetRegistry()
    expect(registry.has('text')).toBe(false)
    registry.register('text', stub)
    expect(registry.has('text')).toBe(true)
    expect(registry.get('text')).toBe(stub)
    expect(registry.names()).toEqual(['text'])
  })

  it('后注册同名控件覆盖先注册（扩展点语义）', () => {
    const registry = new WidgetRegistry()
    const other: WidgetComponent = () => null
    registry.register('json', stub)
    registry.register('json', other)
    expect(registry.get('json')).toBe(other)
  })

  it('取未注册控件抛错（FormRenderer 调用方据此降级 json）', () => {
    const registry = new WidgetRegistry()
    expect(() => registry.get('nope')).toThrow(/未注册控件/)
  })

  it('八件内置控件注册名固定', () => {
    expect([...BUILTIN_WIDGETS]).toEqual([
      'text',
      'number',
      'select',
      'textarea',
      'switch',
      'json',
      'expression',
      'variable-input',
    ])
  })
})
