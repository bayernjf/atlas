import { afterEach, describe, expect, it, vi } from 'vitest'

import { changeLanguage } from '../../locales'
import { saveGraph } from '../apiClient'

function stubValidationError(body: unknown, status = 422) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: false,
      status,
      json: async () => body,
    })),
  )
}

const graph = {} as never

afterEach(() => {
  vi.unstubAllGlobals()
  changeLanguage('zh-CN')
})

describe('Graph DSL 422 错误列表 i18n（docs/17 §2.4）', () => {
  it('中文态按 code 映射并插值 params', async () => {
    stubValidationError({
      detail: ['节点 id 重复：a（兜底中文）'],
      codes: ['NODE_ID_DUPLICATE'],
      params: [{ nodeId: 'a' }],
    })
    await expect(saveGraph(graph)).rejects.toThrow('节点 id 重复：a')
  })

  it('英文态映射英文文案、params 插值、数组 join，且不含汉字', async () => {
    changeLanguage('en-US')
    stubValidationError({
      detail: ['中文兜底'],
      codes: ['PAR_STRATEGY_INVALID'],
      params: [{ strategies: ['wait_all', 'wait_any'] }],
    })
    await expect(saveGraph(graph)).rejects.toThrow(
      'join strategy must be one of: wait_all, wait_any',
    )
    try {
      await saveGraph(graph)
      throw new Error('应当抛错')
    } catch (error) {
      expect(/[一-鿿]/.test((error as Error).message)).toBe(false)
    }
  })

  it('多条错误按当前语言分隔符连接', async () => {
    changeLanguage('en-US')
    stubValidationError({
      detail: ['x', 'y'],
      codes: ['NODE_ID_DUPLICATE', 'NODE_NAME_REQUIRED'],
      params: [{ nodeId: 'a' }, { nodeId: 'b' }],
    })
    try {
      await saveGraph(graph)
      throw new Error('应当抛错')
    } catch (error) {
      const message = (error as Error).message
      expect(message).toContain('; ')
      expect(message).toContain('Duplicate node id: a')
      expect(message).toContain('Node b must have a name')
    }
  })

  it('审批分支枚举 approved/rejected 在中文态本地化', async () => {
    stubValidationError({
      detail: ['x'],
      codes: ['APR_TARGET_SELF'],
      params: [{ branch: 'approved' }],
    })
    await expect(saveGraph(graph)).rejects.toThrow('通过目标不能指向自身')
  })

  it('无 codes（旧后端/非 DSL 422）时回退中文 detail 列表', async () => {
    stubValidationError({ detail: ['裸中文错误甲', '裸中文错误乙'] })
    await expect(saveGraph(graph)).rejects.toThrow('裸中文错误甲；裸中文错误乙')
  })

  it('code 缺少翻译键时回退该条中文 detail，不泄漏 i18n key', async () => {
    changeLanguage('en-US')
    stubValidationError({
      detail: ['兜底中文'],
      codes: ['SOME_UNKNOWN_CODE'],
      params: [{}],
    })
    try {
      await saveGraph(graph)
      throw new Error('应当抛错')
    } catch (error) {
      const message = (error as Error).message
      expect(message).toBe('兜底中文')
      expect(message).not.toContain('dsl.SOME_UNKNOWN_CODE')
    }
  })
})
