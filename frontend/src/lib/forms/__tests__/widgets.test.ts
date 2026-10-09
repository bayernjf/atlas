import { describe, expect, it } from 'vitest'
import { BUILTIN_WIDGETS, type WidgetComponent } from '../types'
import { WidgetRegistry } from '../registry'
import { buildDefaultRegistry, registerWidget, widgetComponent, widgetRegistry } from '../defaultRegistry'

describe('默认控件表（U39① / M4 radio）', () => {
  it('内置控件全部注册且均可取到组件（M3 八件 + M4 radio 共九件）', () => {
    const registry = buildDefaultRegistry()
    for (const name of BUILTIN_WIDGETS) {
      expect(registry.has(name)).toBe(true)
      expect(typeof registry.get(name)).toBe('function')
    }
    expect(registry.names().sort()).toEqual([...BUILTIN_WIDGETS].sort())
  })

  it('模块级默认表与扩展点注册到同一实例', () => {
    const stub: WidgetComponent = () => null
    registerWidget('widgets-ext-same-instance', stub)
    expect(widgetRegistry.get('widgets-ext-same-instance')).toBe(stub)
    // 同实例：全新默认表不含扩展注册，模块级单例持有（扩展点不污染默认表）。
    expect(buildDefaultRegistry().has('widgets-ext-same-instance')).toBe(false)
  })

  it('未注册控件名降级 json（FormRenderer 取组件路径）', () => {
    expect(widgetComponent('nope')).toBe(widgetRegistry.get('json'))
    expect(widgetComponent('text')).toBe(widgetRegistry.get('text'))
    const custom = new WidgetRegistry()
    custom.register('json', widgetRegistry.get('json'))
    expect(widgetComponent('custom-only', custom)).toBe(custom.get('json'))
  })
})
