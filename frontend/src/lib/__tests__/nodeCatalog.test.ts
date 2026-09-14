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

  it('defaults wait to a 5 second duration wait', () => {
    const config = defaultConfig('wait')
    expect(config.waitType).toBe('duration')
    expect(config.durationSeconds).toBe(5)
  })

  it('validates wait duration seconds as integer 1-600', () => {
    expect(validateNode(node('wait'))).toEqual([])

    for (const durationSeconds of [0, -1, 601, 1.5, undefined]) {
      const errors = validateNode(node('wait', { config: { waitType: 'duration', durationSeconds } }))
      expect(errors.some((message) => message.includes('1-600'))).toBe(true)
    }

    const wrongType = validateNode(
      node('wait', { config: { waitType: 'event' as 'duration', durationSeconds: 5 } }),
    )
    expect(wrongType).toContain('等待类型必须为定时等待')
  })

  it('defaults human_approval to 300s reject timeout with empty targets', () => {
    const config = defaultConfig('human_approval')
    expect(config.timeoutSeconds).toBe(300)
    expect(config.onTimeout).toBe('reject')
    expect(config.approvedTarget).toBe('')
    expect(config.rejectedTarget).toBe('')
  })

  it('accepts a fully configured human_approval node', () => {
    const valid = node('human_approval', {
      config: {
        ...defaultConfig('human_approval'),
        summary: '订单 {{trigger-1.context.payload.id}} 退款审批',
        approver: '客服主管',
        timeoutSeconds: 300,
        onTimeout: 'reject',
        approvedTarget: 'tool-approve',
        rejectedTarget: 'tool-reject',
      },
    })
    expect(validateNode(valid)).toEqual([])
  })

  it('validates human_approval summary, timeout range, targets', () => {
    const base = {
      ...defaultConfig('human_approval'),
      summary: '退款审批',
      approvedTarget: 'tool-approve',
      rejectedTarget: 'tool-reject',
    }

    expect(
      validateNode(node('human_approval', { config: { ...base, summary: '  ' } })),
    ).toContain('审批说明必填')

    for (const timeoutSeconds of [9, 3601, 1.5, undefined]) {
      const errors = validateNode(node('human_approval', { config: { ...base, timeoutSeconds } }))
      expect(errors.some((message) => message.includes('10-3600'))).toBe(true)
    }

    const noTargets = validateNode(
      node('human_approval', { config: { ...base, approvedTarget: '', rejectedTarget: '' } }),
    )
    expect(noTargets).toContain('必须选择通过目标')
    expect(noTargets).toContain('必须选择拒绝目标')

    const sameTarget = validateNode(
      node('human_approval', { config: { ...base, rejectedTarget: 'tool-approve' } }),
    )
    expect(sameTarget).toContain('通过目标与拒绝目标不能相同')
  })
})

describe('subgraph', () => {
  it('defaults to empty graphId and empty inputs mapping', () => {
    const config = defaultConfig('subgraph')
    expect(config.graphId).toBe('')
    expect(config.inputs).toEqual({})
  })

  it('accepts a fully configured subgraph node', () => {
    const valid = node('subgraph', {
      config: {
        ...defaultConfig('subgraph'),
        graphId: 'graph-7',
        inputs: { order_id: '{{trigger-1.context.payload.order_id}}' },
      },
    })
    expect(validateNode(valid)).toEqual([])
  })

  it('requires graphId and validates inputs keys/values', () => {
    expect(validateNode(node('subgraph'))).toContain('必须选择引用的已保存子图')

    const emptyKey = node('subgraph', {
      config: { graphId: 'graph-7', inputs: { '': '{{trigger-1.x}}' } },
    })
    expect(validateNode(emptyKey)).toContain('入参键名不能为空')

    const emptyValue = node('subgraph', {
      config: { graphId: 'graph-7', inputs: { order_id: '  ' } },
    })
    expect(validateNode(emptyValue)).toContain('入参 order_id 的映射值不能为空')
  })
})
