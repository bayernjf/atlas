import { describe, expect, it, vi } from 'vitest'

// U1124 机检读源码用 Vite `?raw` 导入（不依赖 node:fs，同 reflection.test.ts 模式）
import embedsLibSource from '../embeds.ts?raw'
import adapterDraftsLibSource from '../adapterDrafts.ts?raw'
import embedPageSource from '../../pages/Embed.tsx?raw'
import embedsConfigPageSource from '../../pages/EmbedsConfig.tsx?raw'
import adapterDraftsPageSource from '../../pages/AdapterDrafts.tsx?raw'
import zhEmbeds from '../../locales/zh-CN/embeds.json'
import enEmbeds from '../../locales/en-US/embeds.json'
import zhAdapterDrafts from '../../locales/zh-CN/adapterDrafts.json'
import enAdapterDrafts from '../../locales/en-US/adapterDrafts.json'

// 数据面测试 mock apiClient（lib 数据函数不触真实网络）
vi.mock('../apiClient')
import { API_WHITELIST, extractEmbedToken } from '../embeds'
import { API_WHITELIST as DRAFT_WHITELIST } from '../adapterDrafts'

describe('打包 BQ U1124：写调用面白名单与深链解析（docs/133 §5，机检非口头承诺）', () => {
  it('lib/embeds.ts 从 apiClient 的 import 面 ⊆ API_WHITELIST 且每名都被引用', () => {
    const importBlock = embedsLibSource.match(/import \{([^}]*)\} from '\.\/apiClient'/)
    expect(importBlock, 'lib/embeds.ts 必须显式 import apiClient 成员').not.toBeNull()
    const imported = importBlock![1]
      .split(',')
      .map((part: string) => part.trim())
      .filter((part: string) => part && !part.startsWith('type '))
      .map((part: string) => part.split(' as ')[0].trim())
      .filter(Boolean)
    for (const name of imported) {
      expect(API_WHITELIST as readonly string[], `import 了非白名单成员: ${name}`).toContain(name)
    }
    for (const name of API_WHITELIST) {
      expect(embedsLibSource, `白名单成员 ${name} 未被引用`).toMatch(new RegExp(`\\b${name}\\b`))
    }
  })

  it('lib/adapterDrafts.ts 从 apiClient 的 import 面 ⊆ API_WHITELIST 且每名都被引用', () => {
    const importBlock = adapterDraftsLibSource.match(/import \{([^}]*)\} from '\.\/apiClient'/)
    expect(importBlock, 'lib/adapterDrafts.ts 必须显式 import apiClient 成员').not.toBeNull()
    const imported = importBlock![1]
      .split(',')
      .map((part: string) => part.trim())
      .filter((part: string) => part && !part.startsWith('type '))
      .map((part: string) => part.split(' as ')[0].trim())
      .filter(Boolean)
    for (const name of imported) {
      expect(DRAFT_WHITELIST as readonly string[], `import 了非白名单成员: ${name}`).toContain(name)
    }
    for (const name of DRAFT_WHITELIST) {
      expect(adapterDraftsLibSource, `白名单成员 ${name} 未被引用`).toMatch(
        new RegExp(`\\b${name}\\b`),
      )
    }
  })

  it('页面不直接 import apiClient（U1124 守护：三页无 apiClient import 面）', () => {
    for (const [name, source] of [
      ['Embed.tsx', embedPageSource],
      ['EmbedsConfig.tsx', embedsConfigPageSource],
      ['AdapterDrafts.tsx', adapterDraftsPageSource],
    ] as const) {
      expect(source, `${name} 不得直接 import apiClient`).not.toMatch(
        /import .* from '\.\.\/lib\/apiClient'/,
      )
    }
  })

  it('extractEmbedToken：/embed/{token} 解析与空段/不匹配回退', () => {
    expect(extractEmbedToken('/embed/abc.def')).toBe('abc.def')
    expect(extractEmbedToken('/embed/a%2Eb')).toBe('a.b')
    expect(extractEmbedToken('/embed/')).toBeNull()
    expect(extractEmbedToken('/approvals/x.y')).toBeNull()
    expect(extractEmbedToken('/dashboard')).toBeNull()
  })

  it('两语 locale 键集一致（embeds / adapterDrafts）', () => {
    const flat = (obj: Record<string, unknown>, prefix = ''): string[] =>
      Object.entries(obj).flatMap(([k, v]) =>
        typeof v === 'object' && v !== null
          ? flat(v as Record<string, unknown>, `${prefix}${k}.`)
          : [`${prefix}${k}`],
      )
    expect(flat(enEmbeds as Record<string, unknown>).sort()).toEqual(
      flat(zhEmbeds as Record<string, unknown>).sort(),
    )
    expect(flat(enAdapterDrafts as Record<string, unknown>).sort()).toEqual(
      flat(zhAdapterDrafts as Record<string, unknown>).sort(),
    )
  })
})
