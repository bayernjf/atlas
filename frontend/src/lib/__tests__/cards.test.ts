import { describe, expect, it } from 'vitest'
import type { CardFormSpec } from '../apiClient'
import {
  cardFormDefaults,
  cardFormSchema,
  cardFormUiSchema,
  missingCardRequired,
} from '../cards'

const FORM: CardFormSpec[] = [
  { type: 'textarea', name: 'comment', label: '审批意见', required: false, default: '' },
  { type: 'input', name: 'ticket', label: '工单号', required: true, default: '' },
  { type: 'input', name: 'note', label: null, required: false, default: '默认备注' },
]

describe('cardFormSchema（卡片 form spec → 扁平 object schema）', () => {
  it('textarea 标内置 textarea 控件、input 不标；根为扁平 object', () => {
    const schema = cardFormSchema(FORM)
    expect(schema.type).toBe('object')
    expect(schema.properties?.comment).toMatchObject({ type: 'string', 'x-widget': 'textarea' })
    expect(schema.properties?.ticket).toMatchObject({ type: 'string' })
    expect(schema.properties?.ticket['x-widget']).toBeUndefined()
    expect(schema.properties?.note.default).toBe('默认备注')
  })

  it('只收集 required 字段；无必填时省略 required', () => {
    expect(cardFormSchema(FORM).required).toEqual(['ticket'])
    const optional = FORM.map((field) => ({ ...field, required: false }))
    expect(cardFormSchema(optional).required).toBeUndefined()
  })

  it('空表单生成空 object（不产出多余 required）', () => {
    const schema = cardFormSchema([])
    expect(schema.type).toBe('object')
    expect(schema.properties).toEqual({})
    expect(schema.required).toBeUndefined()
  })
})

describe('cardFormUiSchema / cardFormDefaults', () => {
  it('label 优先用后端文案，缺省回退字段名', () => {
    expect(cardFormUiSchema(FORM).labels).toEqual({
      comment: '审批意见',
      ticket: '工单号',
      note: 'note',
    })
  })

  it('初始值取 default，缺省空串', () => {
    expect(cardFormDefaults(FORM)).toEqual({ comment: '', ticket: '', note: '默认备注' })
  })
})

describe('missingCardRequired（动作提交前门控）', () => {
  it('仅返回为空的必填字段；空白字符视为空', () => {
    expect(missingCardRequired(FORM, { comment: 'x', ticket: 'T-1', note: '' })).toEqual([])
    const missing = missingCardRequired(FORM, { comment: '', ticket: '   ', note: '' })
    expect(missing.map((field) => field.name)).toEqual(['ticket'])
  })

  it('非必填字段留空不拦', () => {
    expect(missingCardRequired(FORM, { ticket: 'T-9' })).toEqual([])
  })
})
