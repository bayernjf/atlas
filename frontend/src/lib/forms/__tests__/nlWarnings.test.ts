import { describe, expect, it } from 'vitest'
import {
  NL_PARAM_WARNING_CODE,
  nlParamWarnings,
  nlWarningDiagnostics,
  nlWarningPrefix,
} from '../nlWarnings'

// 后端 atlas.llm.nl_generate.validate_param_fills 的真实文案形态
const warnings = [
  '节点「query-1」工具 database/query 参数缺少必填字段：sql',
  '节点「notify-1」工具 message/send 参数字段「channel」类型应为 string',
  '节点「query-1」工具 database/query 参数包含未声明字段：foo',
]

describe('NL paramWarnings 按节点归属（U39⑥）', () => {
  it('前缀取 节点「id」，保持后端文案与顺序', () => {
    expect(nlWarningPrefix('query-1')).toBe('节点「query-1」')
    expect(nlParamWarnings(warnings, 'query-1')).toEqual([warnings[0], warnings[2]])
    expect(nlParamWarnings(warnings, 'notify-1')).toEqual([warnings[1]])
  })

  it('其他节点与空输入取不到警告', () => {
    expect(nlParamWarnings(warnings, 'other-1')).toEqual([])
    expect(nlParamWarnings(undefined, 'query-1')).toEqual([])
    expect(nlParamWarnings([], 'query-1')).toEqual([])
  })

  it('前缀相似的不同节点不误归属（query-1 vs query-10）', () => {
    expect(nlParamWarnings(['节点「query-10」工具 x 参数缺少必填字段：a'], 'query-1')).toEqual([])
  })

  it('转成非阻塞 warning 诊断：无 pointer（挂 params 根）、layer:field、原文案', () => {
    expect(nlWarningDiagnostics(warnings, 'query-1')).toEqual([
      {
        severity: 'warning',
        layer: 'field',
        code: NL_PARAM_WARNING_CODE,
        message: warnings[0],
        loc: {},
      },
      {
        severity: 'warning',
        layer: 'field',
        code: NL_PARAM_WARNING_CODE,
        message: warnings[2],
        loc: {},
      },
    ])
  })
})
