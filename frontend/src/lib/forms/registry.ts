/**
 * WidgetRegistry：注册名 → 控件组件的唯一映射（M3，04 §4.10）。
 *
 * 纯 map 逻辑，不依赖 React，可在 node 环境下单测。默认注册表与八件内置
 * 控件见 widgets.tsx；registerWidget 扩展点导出但 M3 不注册任何业务控件
 * （业务控件/控件市场随 docs/14 D29）。
 */
import type { WidgetComponent } from './types'

export class WidgetRegistry {
  private readonly widgets = new Map<string, WidgetComponent>()

  register(name: string, component: WidgetComponent): void {
    this.widgets.set(name, component)
  }

  has(name: string): boolean {
    return this.widgets.has(name)
  }

  get(name: string): WidgetComponent {
    const component = this.widgets.get(name)
    if (!component) throw new Error(`WidgetRegistry：未注册控件 "${name}"`)
    return component
  }

  names(): string[] {
    return [...this.widgets.keys()]
  }
}
