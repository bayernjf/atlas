import { describe, expect, it } from 'vitest'
import { parseExpression, validateExpression } from '../conditions'

describe('validateExpression', () => {
  it('accepts the first-version whitelist', () => {
    expect(validateExpression('{{trigger-1.context.payload.amount}} > 1000')).toEqual([])
    expect(validateExpression("{{status}} == 'paid' && {{x}} != null")).toEqual([])
    expect(validateExpression('!({{a}} >= 10) || {{b}} < -5')).toEqual([])
  })

  it('reports syntax errors', () => {
    expect(validateExpression('amount >')[0]).toContain('语法错误')
    expect(validateExpression('foo == 1')[0]).toContain('未知标识符')
    expect(validateExpression('(1 > 2')[0]).toContain('括号')
    expect(validateExpression('   ')).toEqual(['表达式不能为空'])
  })

  it('statically rejects literal-only type mismatches but allows variable cases', () => {
    expect(validateExpression("'a' > 1")[0]).toContain('同为数字')
    expect(validateExpression('{{name}} > 1')).toEqual([])
  })

  it('checks precedence and null ordering statically', () => {
    expect(validateExpression('null > 1')[0]).toContain('空值')
    expect(validateExpression('true > false')[0]).toContain('布尔')
  })

  it('D15 accepts arithmetic inside comparisons', () => {
    expect(validateExpression('1 + 2 * 3 == 7')).toEqual([])
    expect(validateExpression('(1 + 2) * 3 == 9')).toEqual([])
    expect(validateExpression('10 / 4 == 2.5')).toEqual([])
    expect(validateExpression('7 % 3 == 1')).toEqual([])
    expect(validateExpression('-7 % 3 == -1')).toEqual([])
    expect(validateExpression('{{x}} * 2 > 10')).toEqual([])
    expect(validateExpression('-5 < 1')).toEqual([])
  })

  it('D15 rejects non-boolean top-level arithmetic/constants', () => {
    expect(validateExpression('1 + 1')[0]).toContain('布尔值')
    expect(validateExpression('-5 + 1')[0]).toContain('布尔值')
    expect(validateExpression("'hello'")[0]).toContain('布尔值')
    expect(validateExpression('abs(-3)')[0]).toContain('布尔值')
    expect(validateExpression('date(2026,1,1)')[0]).toContain('布尔值')
    expect(validateExpression('{{x}} + 1')[0]).toContain('布尔值')
    expect(validateExpression('{{x}}')).toEqual([])
  })

  it('D15 constant-folds arithmetic type and divide-by-zero errors', () => {
    expect(validateExpression('1 / 0 == 0')[0]).toContain('除数不能为 0')
    expect(validateExpression('1 % 0 == 0')[0]).toContain('模数不能为 0')
    expect(validateExpression("'a' + 1 == 'a1'")[0]).toContain('均为数值')
    expect(validateExpression('{{s}} + 1 == 2')).toEqual([])
  })

  it('D15 accepts numeric/string whitelist functions inside comparisons', () => {
    expect(validateExpression('abs(-3) == 3')).toEqual([])
    expect(validateExpression('floor(2.8) == 2')).toEqual([])
    expect(validateExpression('ceil(2.1) == 3')).toEqual([])
    expect(validateExpression('round(2.5) == 3')).toEqual([])
    expect(validateExpression('round(-1.5) == -1')).toEqual([])
    expect(validateExpression('min(3, 1, 2) == 1')).toEqual([])
    expect(validateExpression('max(3, 1, 2) == 3')).toEqual([])
    expect(validateExpression("len('hello') == 5")).toEqual([])
    expect(validateExpression("lower('AbC') == 'abc'")).toEqual([])
    expect(validateExpression("upper('AbC') == 'ABC'")).toEqual([])
    expect(validateExpression('len(1) == 1')[0]).toContain('字符串/数组/对象')
  })

  it('D15 handles deterministic date functions and comparisons', () => {
    expect(validateExpression('date(2026, 1, 1) < date(2026, 2, 1)')).toEqual([])
    expect(validateExpression('year(date(2026, 9, 19)) == 2026')).toEqual([])
    expect(validateExpression('daysBetween(date(2026,1,1), date(2026,1,11)) == 10')).toEqual([])
    expect(validateExpression('daysBetween(date(2026,1,11), date(2026,1,1)) == -10')).toEqual([])
    expect(validateExpression('date(2026, 13, 1) < date(2027,1,1)')[0]).toContain('非法日期')
    expect(validateExpression('date(2026,1,1) > 1')[0]).toContain('同为数字')
    expect(validateExpression('year(1) == 2026')[0]).toContain('要求日期值')
  })

  it('D15 rejects unknown functions and bad arity', () => {
    expect(validateExpression('foo(1) == 1')[0]).toContain('未知函数')
    expect(validateExpression('abs() == 0')[0]).toContain('参数')
    expect(validateExpression('date(2026, 1) == date(2026,1)')[0]).toContain('3 个参数')
    expect(validateExpression('bar == 1')[0]).toContain('未知标识符')
  })

  // C（docs/27 §2）：可注入时钟 today/now + UTC datetime 体系（前端静态校验同构）
  it('C accepts today/now/datetime/hoursBetween in boolean comparisons', () => {
    expect(validateExpression('now() >= datetime(2026,1,1,0,0)')).toEqual([])
    expect(validateExpression('today() == date(2026,9,19) || now() < datetime(2026,9,20,0,0)')).toEqual([])
    expect(validateExpression('hoursBetween(datetime(2026,9,19,10,0), datetime(2026,9,19,12,30)) == 2.5')).toEqual([])
    expect(validateExpression('hoursBetween(date(2026,9,19), datetime(2026,9,19,6,0)) == 6')).toEqual([])
    expect(validateExpression('daysBetween(datetime(2026,9,19,23,0), datetime(2026,9,20,1,0)) == 1')).toEqual([])
    expect(validateExpression('year(now()) == 2026 || month(now()) == 9')).toEqual([])
  })

  it('C rejects non-boolean bare today()/now() and datetime arity', () => {
    expect(validateExpression('now()')[0]).toContain('布尔')
    expect(validateExpression('today()')[0]).toContain('布尔')
    expect(validateExpression('datetime(2026,1,1,0) == now()')[0]).toContain('参数') // 仅 4 参
    expect(validateExpression('datetime(2026,1,1,0,0,0,0) == now()')[0]).toContain('参数') // 7 参
    expect(validateExpression('today(1) == true')[0]).toContain('参数')
  })

  it('C constant-folds invalid datetime and non-integer components', () => {
    expect(validateExpression('datetime(2026,1,1,25,0) == now()')[0]).toContain('非法日期时间')
    expect(validateExpression('datetime(2026,13,1,0,0) == now()')[0]).toContain('非法日期')
    expect(validateExpression('datetime(2026.5,1,1,0,0) == now()')[0]).toContain('整数')
  })

  it('C rejects hoursBetween type errors and date/datetime cross-type ordering', () => {
    expect(validateExpression('hoursBetween(1, now()) > 0')[0]).toContain('hoursBetween')
    expect(validateExpression('today() > now()')[0]).toContain('日期时间')
    expect(validateExpression('date(2026,9,19) < now()')[0]).toContain('日期时间')
  })

  it('docs/28 §4.2 custom alert DSL: whitelist expressions pass static validation (unknown vars allowed)', () => {
    // 自定义规则白名单变量在静态校验期类型未知，须放行；运行期由后端注入 status/durationMs/failedCount/hasError
    expect(validateExpression("{{status}} == 'error' || {{hasError}}")).toEqual([])
    expect(validateExpression('{{durationMs}} > 500')).toEqual([])
    expect(validateExpression('{{failedCount}} >= 1')).toEqual([])
    expect(validateExpression('{{notInWhitelist}} == 1')).toEqual([])
    // 纯算术（非布尔顶层）仍拒绝，与条件引擎一致
    expect(validateExpression('{{durationMs}} + 1')[0]).toContain('布尔值')
  })

  it('docs/45 parseExpression is syntax-only: arrays paths pass, malformed input errors', () => {
    expect(parseExpression('{{global.order_ids}}')).toBeNull()
    expect(parseExpression('1 + 2')).toBeNull()
    expect(parseExpression('{{global.ids}')).toContain('语法错误')
  })
})
