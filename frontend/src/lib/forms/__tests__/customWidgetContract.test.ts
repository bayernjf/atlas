import { afterEach, describe, expect, it, vi } from 'vitest'
import { buildDefaultRegistry, registerWidget, widgetComponent } from '../defaultRegistry'
import { buildNodeRegistry } from '../nodeRegistry'
import { resolveWidget } from '../resolveWidget'
import { WidgetRegistry } from '../registry'
import { BUSINESS_WIDGETS, BUILTIN_WIDGETS, isWidgetName, TARGET_SELECT_WIDGET, type WidgetComponent } from '../types'

/**
 * 自定义控件扩展契约（docs/118 §3）落码测试。
 *
 * 覆盖契约清单：未注册降级 json＋warn、同名覆盖、禁内置名、组件签名校验、
 * tool 源忽略业务 x-widget、resolveWidget 未知名按类型默认走、注册与
 * BUSINESS_WIDGETS 同源。
 */

const stub: WidgetComponent = () => null

afterEach(() => {
  vi.restoreAllMocks()
})

describe('isWidgetName（docs/118 §3.2 类型登记）', () => {
  it('内置九件名全部命中', () => {
    for (const name of BUILTIN_WIDGETS) expect(isWidgetName(name)).toBe(true)
  })

  it('已登记业务控件名命中（target-select 等）', () => {
    for (const name of BUSINESS_WIDGETS) expect(isWidgetName(name)).toBe(true)
  })

  it('未登记名不命中（含空串与非字符串）', () => {
    expect(isWidgetName('bogus-widget')).toBe(false)
    expect(isWidgetName('')).toBe(false)
    expect(isWidgetName(42)).toBe(false)
    expect(isWidgetName(undefined)).toBe(false)
  })
})

describe('registerWidget 静态校验（docs/118 §3.1）', () => {
  it('拒绝注册内置控件名（禁覆盖）', () => {
    expect(() => registerWidget('text', stub)).toThrow(/内置控件冲突/)
  })

  it('拒绝非函数组件', () => {
    expect(() => registerWidget('gate-metric-table', 'not-a-fn' as unknown as WidgetComponent)).toThrow(/函数组件/)
  })

  it('拒绝空名', () => {
    expect(() => registerWidget('', stub)).toThrow(/非空字符串/)
  })

  it('同名覆盖：后注册者生效（扩展点语义保留）', () => {
    const first: WidgetComponent = () => null
    const second: WidgetComponent = () => null
    registerWidget('contract-override', first)
    registerWidget('contract-override', second)
    expect(widgetComponent('contract-override')).toBe(second)
  })
})

describe('widgetComponent 运行期降级（docs/118 §3.4）', () => {
  it('未注册名降级 json 且 console.warn 一次', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const component = widgetComponent('no-such-widget')
    expect(component).toBe(widgetComponent('json'))
    expect(warn).toHaveBeenCalledTimes(1)
    expect(warn).toHaveBeenCalledWith(expect.stringContaining('no-such-widget'))
  })

  it('json 名本身不 warn（正常路径）', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    expect(widgetComponent('json')).toBeTruthy()
    expect(warn).not.toHaveBeenCalled()
  })

  it('已注册业务控件名返回对应组件、不 warn', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined)
    const registry = buildNodeRegistry()
    expect(widgetComponent(TARGET_SELECT_WIDGET, registry)).toBeTruthy()
    expect(warn).not.toHaveBeenCalled()
  })
})

describe('resolveWidget x-widget 守卫（docs/118 §3.2，消除断言绕过）', () => {
  it('node 源已知业务名 → widget', () => {
    const result = resolveWidget({ type: 'string', 'x-widget': TARGET_SELECT_WIDGET }, 'node')
    expect(result).toEqual({ kind: 'widget', widget: TARGET_SELECT_WIDGET })
  })

  it('node 源未登记名 → 不再强制进控件分支，按类型默认走（string → text）', () => {
    const result = resolveWidget({ type: 'string', 'x-widget': 'bogus-widget' }, 'node')
    expect(result).toEqual({ kind: 'widget', widget: 'text' })
  })

  it('tool 源忽略业务 x-widget（Capability 拒绝一切 x-*）', () => {
    const result = resolveWidget({ type: 'string', 'x-widget': TARGET_SELECT_WIDGET }, 'tool')
    expect(result).toEqual({ kind: 'widget', widget: 'text' })
  })

  it('node 源内置 x-widget 仍最优先（既有语义不回归）', () => {
    const result = resolveWidget({ type: 'string', 'x-widget': 'textarea' }, 'node')
    expect(result).toEqual({ kind: 'widget', widget: 'textarea' })
  })
})

describe('注册与 BUSINESS_WIDGETS 同源（docs/118 §3.2 守护）', () => {
  it('buildNodeRegistry 注册名集合恰好覆盖全部业务控件名', () => {
    const registry = buildNodeRegistry()
    const names = registry.names()
    for (const name of BUSINESS_WIDGETS) expect(names).toContain(name)
  })

  it('WidgetRegistry.get 对未注册名 throw 语义保留（开发期 fail-fast）', () => {
    const registry = new WidgetRegistry()
    expect(() => registry.get('nope')).toThrow(/未注册控件/)
  })

  it('buildDefaultRegistry 不含业务控件（工具表单不污染）', () => {
    const registry = buildDefaultRegistry()
    for (const name of BUSINESS_WIDGETS) expect(registry.has(name)).toBe(false)
  })
})
