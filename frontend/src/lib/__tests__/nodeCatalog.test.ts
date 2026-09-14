import { describe, expect, it } from 'vitest'
import { defaultConfig, defaultRetry, validateNode, type EditorNodeData } from '../nodeCatalog'

function node(kind: EditorNodeData['kind'], patch: Partial<EditorNodeData> = {}): EditorNodeData {
  return {
    label: '节点',
    kind,
    status: 'idle',
    config: defaultConfig(kind),
    retry: defaultRetry(),
    ...patch,
  }
}

describe('defaultConfig', () => {
  it('provides type-specific defaults (confidence threshold 0.6 per 06 §6.2)', () => {
    expect(defaultConfig('trigger').triggerType).toBe('manual')
    expect(defaultConfig('ai_decision').confidenceThreshold).toBe(0.6)
    expect(defaultConfig('tool_call').tool).toBe('')
  })
})

describe('validateNode', () => {
  it('requires a label', () => {
    expect(validateNode(node('trigger', { label: '  ' }))).toContain('节点名称必填')
  })

  it('requires cron for schedule trigger', () => {
    const invalid = node('trigger', {
      config: { ...defaultConfig('trigger'), triggerType: 'schedule', cron: '' },
    })
    const valid = node('trigger', {
      config: { ...defaultConfig('trigger'), triggerType: 'schedule', cron: '0 9 * * *' },
    })
    expect(validateNode(invalid)).toContain('定时触发必须填写 Cron 表达式')
    expect(validateNode(valid)).toEqual([])
  })

  it('requires webhook url for webhook trigger', () => {
    const errors = validateNode(
      node('trigger', {
        config: { ...defaultConfig('trigger'), triggerType: 'webhook', webhookUrl: '' },
      }),
    )
    expect(errors).toContain('Webhook 触发必须填写 URL')
  })

  it('requires prompt template for ai_decision', () => {
    expect(validateNode(node('ai_decision'))).toContain('AI 决策必须填写提示词模板')
    expect(
      validateNode(
        node('ai_decision', {
          config: { ...defaultConfig('ai_decision'), promptTemplate: '通过吗？' },
        }),
      ),
    ).toEqual([])
  })

  it('rejects out-of-range confidence threshold', () => {
    const errors = validateNode(
      node('ai_decision', {
        config: { ...defaultConfig('ai_decision'), promptTemplate: 'x', confidenceThreshold: 1.2 },
      }),
    )
    expect(errors).toContain('置信度阈值需在 0-1 之间')
  })

  it('requires tool selection for tool_call', () => {
    expect(validateNode(node('tool_call'))).toContain('工具调用必须选择工具')
    expect(
      validateNode(
        node('tool_call', { config: { ...defaultConfig('tool_call'), tool: 'web/click' } }),
      ),
    ).toEqual([])
  })

  it('validates condition branches and default target within node config', () => {
    expect(validateNode(node('condition'))).toContain('第 1 个分支名称不能为空')
    const valid = node('condition', {
      config: {
        branches: [
          { label: '大额', expression: '{{trigger-1.context.payload.amount}} > 1000', target: 'tool-human' },
        ],
        defaultTarget: 'tool-auto',
      },
    })
    expect(validateNode(valid)).toEqual([])
  })

  it('rejects duplicate labels, bad expressions and target/default collision', () => {
    const errors = validateNode(
      node('condition', {
        config: {
          branches: [
            { label: 'x', expression: 'amount >', target: 'a' },
            { label: 'x', expression: '{{ok}} == null', target: 'a' },
          ],
          defaultTarget: 'a',
        },
      }),
    )
    expect(errors.some((message) => message.includes('分支名称重复'))).toBe(true)
    expect(errors.some((message) => message.includes('语法错误'))).toBe(true)
    expect(errors.some((message) => message.includes('分支目标重复'))).toBe(true)
    expect(errors).toContain('默认分支目标不能与其他分支相同')
  })

  it('validates loop expression, iteration cap and body/exit targets', () => {
    expect(validateNode(node('loop'))).toEqual(
      expect.arrayContaining([
        '必须填写继续条件表达式',
        '必须选择循环体入口',
        '必须选择退出目标',
      ]),
    )
    const valid = node('loop', {
      config: {
        mode: 'while',
        continueExpression: '{{loop-1.index}} < 3',
        maxIterations: 10,
        bodyTarget: 'tool-body',
        exitTarget: 'tool-exit',
      },
    })
    expect(validateNode(valid)).toEqual([])

    const errors = validateNode(
      node('loop', {
        config: {
          mode: 'while',
          continueExpression: 'index >',
          maxIterations: 0,
          bodyTarget: 'same',
          exitTarget: 'same',
        },
      }),
    )
    expect(errors.some((message) => message.includes('语法错误'))).toBe(true)
    expect(errors.some((message) => message.includes('1-100'))).toBe(true)
    expect(errors).toContain('循环体入口与退出目标不能相同')
  })

  it('defaults parallel to two branches and all_success strategy', () => {
    const config = defaultConfig('parallel')
    expect(config.joinStrategy).toBe('all_success')
    expect(config.branches).toHaveLength(2)
    expect(config.joinTarget).toBe('')
  })

  it('validates parallel branches and join target', () => {
    expect(validateNode(node('parallel'))).toEqual(
      expect.arrayContaining([
        '第 1 个分支名称不能为空',
        '分支 第 1 个分支 必须选择目标节点',
        '第 2 个分支名称不能为空',
        '分支 第 2 个分支 必须选择目标节点',
        '必须选择汇聚目标',
      ]),
    )
    const valid = node('parallel', {
      config: {
        joinStrategy: 'all_completed',
        branches: [
          { label: 'A', target: 'tool-a' },
          { label: 'B', target: 'tool-b' },
        ],
        joinTarget: 'tool-join',
      },
    })
    expect(validateNode(valid)).toEqual([])

    const errors = validateNode(
      node('parallel', {
        config: {
          joinStrategy: 'all_success',
          branches: [
            { label: '同', target: 'tool-x' },
            { label: '同', target: 'tool-x' },
          ],
          joinTarget: 'tool-x',
        },
      }),
    )
    expect(errors).toContain('分支名称重复：同')
    expect(errors).toContain('分支目标重复：tool-x')
    expect(errors).toContain('汇聚目标不能与任一分支目标相同')

    const single = validateNode(
      node('parallel', { config: { joinStrategy: 'all_success', branches: [{ label: 'A', target: 'a' }], joinTarget: 'j' } }),
    )
    expect(single.some((message) => message.includes('2-10'))).toBe(true)
  })
})
