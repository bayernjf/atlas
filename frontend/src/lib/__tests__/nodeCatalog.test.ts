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
})
