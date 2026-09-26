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

  it('cron 不合法：中文态把后端的 reason 插进本语言模板（docs/68 打包 N）', async () => {
    stubValidationError({
      detail: ["Cron 表达式 '5/2 * * * *' 无效：分钟字段的 '5/2' 不在支持的语法内"],
      codes: ['NODE_TRIGGER_CRON_INVALID'],
      params: [{ owner: 'trigger-1', reason: '分钟字段的 \'5/2\' 不在支持的语法内' }],
    })
    await expect(saveGraph(graph)).rejects.toThrow('节点 trigger-1：分钟字段的')
  })

  /**
   * 英文态这条刻意**不**断"不含汉字"：cron 只有一份解释器（后端 `scheduling/cron.py`），
   * 理由就是不让两处实现对边角分叉，所以解释文本必然来自后端。这里断的是**模板**为英文、
   * reason 原样透传——前端复刻解析器才是该防的事。
   */
  it('cron 不合法：英文态用英文模板包后端 reason，不泄漏 key', async () => {
    changeLanguage('en-US')
    stubValidationError({
      detail: ['兜底中文'],
      codes: ['NODE_TRIGGER_CRON_INVALID'],
      params: [{ owner: 'trigger-1', reason: '需要 5 个字段（分 时 日 月 周），当前 4 个' }],
    })
    try {
      await saveGraph(graph)
      throw new Error('应当抛错')
    } catch (error) {
      const message = (error as Error).message
      expect(message.startsWith('Node trigger-1: ')).toBe(true)
      expect(message).toContain('需要 5 个字段')
      expect(message).not.toContain('dsl.NODE_TRIGGER_CRON_INVALID')
    }
  })
})
