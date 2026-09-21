import { describe, expect, it } from 'vitest'
import { ONCALL_MEMBERS_LIMIT, parseMembers, validateMembers } from '../onCall'

describe('parseMembers', () => {
  it('空串/纯空白返回空数组', () => {
    expect(parseMembers('')).toEqual([])
    expect(parseMembers('   \n\t ')).toEqual([])
  })

  it('支持英文逗号、中文逗号、分号、换行与空白分隔', () => {
    expect(parseMembers('a,b，c；d;e\nf\tg')).toEqual(['a', 'b', 'c', 'd', 'e', 'f', 'g'])
  })

  it('去首尾空白并丢弃空段', () => {
    expect(parseMembers('  a  , ,b,\n c ')).toEqual(['a', 'b', 'c'])
  })

  it('去重并保持首次出现顺序', () => {
    expect(parseMembers('a,b,a,c,b')).toEqual(['a', 'b', 'c'])
    expect(parseMembers('a , a ,a')).toEqual(['a'])
  })
})

describe('validateMembers', () => {
  it('空列表报 empty', () => {
    expect(validateMembers([])).toBe('onCall.error.empty')
  })

  it('超上限报 tooMany', () => {
    expect(validateMembers(Array.from({ length: ONCALL_MEMBERS_LIMIT + 1 }, (_, i) => `u${i}`))).toBe(
      'onCall.error.tooMany',
    )
  })

  it('1 至上限合法', () => {
    expect(validateMembers(['a'])).toBeNull()
    expect(validateMembers(Array.from({ length: ONCALL_MEMBERS_LIMIT }, (_, i) => `u${i}`))).toBeNull()
  })
})
