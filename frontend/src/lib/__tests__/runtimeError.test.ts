import { afterEach, describe, expect, it } from 'vitest'

import { changeLanguage } from '../../locales'
import {
  isRuntimeErrorCode,
  resolveExpressionErrors,
  resolveRuntimeDetail,
  resolveRuntimeError,
} from '../runtimeError'

afterEach(() => {
  changeLanguage('zh-CN')
})

describe('运行期错误码 i18n（docs/60 G1）', () => {
  it('中文态把 COND_* 码映射为中文文案并插值', () => {
    const text = resolveRuntimeError(
      'COND_DIVIDE_BY_ZERO',
      { op: '/' },
      '兜底中文 message',
    )
    expect(text).toBe('算术“/”的除数不能为 0')
  })

  it('英文态映射英文文案、插值 params，且不含汉字', () => {
    changeLanguage('en-US')
    const text = resolveRuntimeError(
      'COND_SYNTAX_UNEXPECTED_CHAR',
      { token: '@', pos: 3 },
      'fallback zh message',
    )
    expect(text).toBe('Syntax error: unexpected character "@" (position 3)')
    expect(text).not.toMatch(/[一-鿿]/)
  })

  it('WAIT_* 与 LOOP_* 码英文态均为英文', () => {
    changeLanguage('en-US')
    expect(resolveRuntimeError('WAIT_TIMEOUT_FAILED', undefined, '超时')).toBe(
      'Timed out waiting for the event',
    )
    expect(resolveRuntimeError('LOOP_ITEMS_TOO_LARGE', undefined, '超长')).toBe(
      'The iterated array exceeds the maximum length',
    )
    expect(resolveRuntimeError('RUNTIME_UNEXPECTED', undefined, 'boom')).toBe(
      'An unexpected runtime error occurred',
    )
  })

  it('LLM_* 码（LLM 决策/分类不可用）双语映射，英文态不含汉字', () => {
    expect(resolveRuntimeError('LLM_DECISION_UNAVAILABLE', undefined, '兜底')).toContain(
      '生产环境未配置 LLM 决策器',
    )
    expect(resolveRuntimeError('LLM_CLASSIFIER_UNAVAILABLE', undefined, '兜底')).toContain(
      '生产环境未配置 LLM 分类器',
    )
    changeLanguage('en-US')
    const en = resolveRuntimeError('LLM_DECISION_UNAVAILABLE', undefined, '兜底')
    expect(en).toContain('No LLM decision provider is configured in production')
    expect(en).not.toMatch(/[一-鿿]/)
    const enCond = resolveRuntimeError('LLM_CLASSIFIER_UNAVAILABLE', undefined, '兜底')
    expect(enCond).toContain('No LLM classifier is configured in production')
    expect(enCond).not.toMatch(/[一-鿿]/)
  })

  it('TYPE_MISMATCH 运算符类型错误：双语 detail 组装、类型码本地化', () => {
    const zh = resolveRuntimeError(
      'COND_TYPE_MISMATCH',
      { op: '+', expected: 'number', actual: 'string' },
      '兜底',
    )
    expect(zh).toContain('类型不匹配')
    expect(zh).toContain('运算符“+”')
    expect(zh).toContain('数值')
    expect(zh).toContain('字符串')

    changeLanguage('en-US')
    const en = resolveRuntimeError(
      'COND_TYPE_MISMATCH',
      { op: '+', expected: 'number', actual: 'string' },
      'fallback',
    )
    expect(en).toBe("Type mismatch: operator '+' expects number, got string")
    expect(en).not.toMatch(/[一-鿿]/)
  })

  it('TYPE_MISMATCH 函数类型错误与组合期望类型本地化', () => {
    const zh = resolveRuntimeError(
      'COND_TYPE_MISMATCH',
      { func: 'len', expected: 'string|array|object', actual: 'number' },
      '兜底',
    )
    expect(zh).toContain('函数“len”')
    expect(zh).toContain('字符串/数组/对象')

    changeLanguage('en-US')
    const en = resolveRuntimeError(
      'COND_TYPE_MISMATCH',
      { func: 'len', expected: 'string|array|object', actual: 'number' },
      'fallback',
    )
    expect(en).toContain('function len()')
    expect(en).toContain('string | array | object')
  })

  it('date 分量整数错误按 component 本地化（英文不泄漏中文分量名）', () => {
    const zh = resolveRuntimeError(
      'COND_TYPE_MISMATCH',
      { func: 'date', expected: 'integer', component: 'month' },
      '兜底',
    )
    expect(zh).toContain('月份')
    changeLanguage('en-US')
    const en = resolveRuntimeError(
      'COND_TYPE_MISMATCH',
      { func: 'date', expected: 'integer', component: 'month' },
      'fallback',
    )
    expect(en).toBe('Type mismatch: the month argument of date() must be integer')
    expect(en).not.toMatch(/[一-鿿]/)
  })

  it('U1152：成员判定＝"有没有译文"：无码、空码、目录外的码一律回退后端中文兜底', () => {
    expect(resolveRuntimeError(undefined, undefined, '后端中文')).toBe('后端中文')
    expect(resolveRuntimeError('', undefined, '后端中文')).toBe('后端中文')
    expect(resolveRuntimeError('SOME_OTHER_CODE', undefined, '后端中文')).toBe('后端中文')
    // 打包 AW（docs/89 A-4）改判：识别从"前缀白名单"改成"目录成员"。所以一个编造的
    // COND_FOO / WAIT_X 不再是"可翻译"——它们没有任何文案，判 true 只会让下一位以为
    // 补了键就能出英文。真键才 true。
    expect(isRuntimeErrorCode('COND_FOO')).toBe(false)
    expect(isRuntimeErrorCode('WAIT_X')).toBe(false)
    expect(isRuntimeErrorCode('LLM_DECISION_UNAVAILABLE')).toBe(true)
    expect(isRuntimeErrorCode('AUTH_BAD')).toBe(false)
    expect(isRuntimeErrorCode(undefined)).toBe(false)
  })

  it('U1152：A-4 目录里新增的码在英文态出英文（旧前缀白名单让它们永远走中文兜底）', () => {
    changeLanguage('zh-CN')
    const enOnly: Array<[string, string]> = [
      ['SUBGRAPH_SUSPEND_UNSUPPORTED', 'suspend point inside a subgraph'],
      ['OPENAPI_DUPLICATE', 'duplicates an existing'],
      ['OPENAPI_NO_IMPORTABLE_OPERATION', 'No importable operations'],
      ['WAIT_ALREADY_SIGNALED', 'already been signalled'],
      ['WAIT_TOKEN_NOT_FOUND', 'does not exist or has already been cleaned up'],
    ]
    for (const [code, needle] of enOnly) {
      const zh = resolveRuntimeError(code, undefined, '后端中文兜底')
      expect(zh, `${code} 中文态必须走目录键而不是兜底`).not.toBe('后端中文兜底')
      changeLanguage('en-US')
      const en = resolveRuntimeError(code, undefined, '后端中文兜底')
      expect(en.toLowerCase(), `${code} 英文态`).toContain(needle.toLowerCase())
      expect(en, `${code} 英文态不残留汉字`).not.toMatch(/[一-鿿]/)
      expect(en, `${code} 不泄漏占位符`).not.toMatch(/\{\{|\}\}/)
      changeLanguage('zh-CN')
    }
    // 模板带占位符的码要按它自己通道的真实形状喂 params——上面那些码的文案都不带占位符，
    // 所以这一条单列：不带 params 时必须回退，带了才出英文。
    changeLanguage('en-US')
    expect(resolveRuntimeError('COND_INVALID_RANGE', undefined, 'backend fallback')).toBe(
      'backend fallback',
    )
    expect(
      resolveRuntimeError('COND_INVALID_RANGE', { func: 'randint', low: 9, high: 1 }, 'fb'),
    ).toBe('Function "randint": the lower bound cannot exceed the upper bound (9 > 1)')
    changeLanguage('zh-CN')
  })

  it('resolveRuntimeDetail 兼容字符串与 {code,message,params} 对象', () => {
    expect(resolveRuntimeDetail('纯字符串')).toBe('纯字符串')
    expect(
      resolveRuntimeDetail({ code: 'WAIT_DURATION_INVALID', message: '中文兜底' }),
    ).toBe('等待节点的等待时长非法（须为未来 1–3600 秒）')
    changeLanguage('en-US')
    expect(
      resolveRuntimeDetail({ code: 'COND_NULL_COMPARISON', message: 'zh' }),
    ).not.toMatch(/[一-鿿]/)
  })

  it('resolveExpressionErrors 与 expression_errors 等长、逐码解析，无码/缺参回退中文', () => {
    // COND_NULL_COMPARISON 无占位可直接解析；COND_DIVIDE_BY_ZERO 需 {{op}} 但节点结果
    // 通道不带 params，残留占位时回退该条后端中文明细。
    const codes = ['COND_NULL_COMPARISON', '', 'UNRECOGNIZED', 'COND_DIVIDE_BY_ZERO']
    const messages = ['中文空值', '中文无码', '中文未知', '中文除零']
    const resolved = resolveExpressionErrors(codes, messages)
    expect(resolved).toHaveLength(4)
    expect(resolved[0]).toBe('空值（null/缺失变量）只能做相等比较，不能参与大小比较')
    expect(resolved[1]).toBe('中文无码')
    expect(resolved[2]).toBe('中文未知')
    expect(resolved[3]).toBe('中文除零')
    expect(resolved[3]).not.toContain('{{')
    // 缺 codes 时整体回退中文 messages
    expect(resolveExpressionErrors(undefined, ['a', 'b'])).toEqual(['a', 'b'])
  })

  it('U1154：AX 给节点结果通道补了 params，参数化模板这才真填得满', () => {
    const codes = ['COND_TYPE_MISMATCH', 'COND_DIVIDE_BY_ZERO', 'COND_NULL_COMPARISON']
    const messages = ['中文类型不符', '中文除零', '中文空值']
    const params = [{ op: '!', expected: 'boolean', actual: 'string' }, { op: '/' }, {}]

    changeLanguage('en-US')
    const en = resolveExpressionErrors(codes, messages, params)
    expect(en[0]).toContain('boolean')
    expect(en[0]).toContain('string')
    expect(en[0]).not.toMatch(/[一-鿿]/)
    expect(en[1]).toContain('/')
    expect(en[2]).not.toMatch(/[一-鿿]/)
    for (const text of en) expect(text).not.toMatch(/\{\{|\}\}/)

    // 缺位／非对象条目一律按"无参"处理：填不满就回退后端原文，绝不把占位放上屏
    expect(resolveExpressionErrors(['COND_DIVIDE_BY_ZERO'], ['中文除零'], [null])).toEqual([
      '中文除零',
    ])
    expect(resolveExpressionErrors(['COND_DIVIDE_BY_ZERO'], ['中文除零'])).toEqual(['中文除零'])
    expect(resolveExpressionErrors(['COND_DIVIDE_BY_ZERO'], ['中文除零'], 'not-an-array')).toEqual([
      '中文除零',
    ])

    changeLanguage('zh-CN')
    const zh = resolveExpressionErrors(codes, messages, params)
    expect(zh[0]).toContain('布尔值')
    expect(zh[1]).toContain('除数')
  })
})
