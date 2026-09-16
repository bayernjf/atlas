import { describe, expect, it } from 'vitest'
import { BUILTIN_WIDGETS } from '../types'
import { buildDefaultRegistry, registerWidget, widgetRegistry } from '../defaultRegistry'

describe('默认控件表（U39①）', () => {
  it('八件内置控件全部注册且均可取到组件', () => {
    const registry = buildDefaultRegistry()
    for (const name of BUILTIN_WIDGETS) {
      expect(registry.has(name)).toBe(true)
      expect(typeof registry.get(name)).toBe('function')
    }
    expect(registry.names().sort()).toEqual([...BUILTIN_WIDGETS].sort())
  })

  it('模块级默认表与扩展点注册到同一实例', () => {
    const original = widgetRegistry.get('text')
    registerWidget('text', original)
    expect(widgetRegistry.get('text')).toBe(original)
  })
})
