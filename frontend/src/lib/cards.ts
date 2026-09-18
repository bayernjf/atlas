/**
 * M8 交互卡片运行态表单适配（04 §5.6 追加段 / 12 §3.11 / 03 `form_renderer`）。
 *
 * 卡片 form sections 是后端 render_card 投影出的扁平 spec（textarea/input），
 * 这里把它翻译成 FormRenderer 可消费的「扁平 object MetaSchema + UISchema」，
 * 作为继 node/tool 之后的第三来源 source='card'：只用内置控件、无递归/数组、
 * 无节点业务控件。全部为纯函数，可在 node 环境单测。
 */
import type { CardFormSpec } from './apiClient'
import type { MetaSchema } from './schemas/metaSchema'
import type { UiSchema } from './forms/uiSchema'

/** 卡片 form spec → 扁平 object schema（v1 仅一层 string 字段；textarea 走内置多行控件）。 */
export function cardFormSchema(form: CardFormSpec[]): MetaSchema {
  const properties: Record<string, MetaSchema> = {}
  const required: string[] = []
  for (const field of form) {
    properties[field.name] = {
      type: 'string',
      default: field.default ?? '',
      ...(field.type === 'textarea' ? { 'x-widget': 'textarea' } : {}),
    }
    if (field.required) required.push(field.name)
  }
  return {
    type: 'object',
    properties,
    ...(required.length > 0 ? { required } : {}),
  }
}

/** 卡片字段中文标签（后端 label 优先，缺省回退字段名）。 */
export function cardFormUiSchema(form: CardFormSpec[]): UiSchema {
  const labels: Record<string, string> = {}
  for (const field of form) labels[field.name] = field.label ?? field.name
  return { labels }
}

/** 表单初始值：取后端 default，缺省空串。 */
export function cardFormDefaults(form: CardFormSpec[]): Record<string, string> {
  const values: Record<string, string> = {}
  for (const field of form) values[field.name] = field.default ?? ''
  return values
}

/** 返回当前值下仍为空的必填字段（动作提交前的前端门控；后端 map_action_output 另有权威校验）。 */
export function missingCardRequired(
  form: CardFormSpec[],
  values: Record<string, unknown>,
): CardFormSpec[] {
  return form.filter(
    (field) => field.required && !String(values[field.name] ?? '').trim(),
  )
}
