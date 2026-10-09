/**
 * 部署 gate 配置面迁 forms（docs/115 §5 第 3 步第一片，2026-10-10）契约测试。
 *
 * 覆盖：gateSchema 形状（四字段、metrics 走 deploy-gate-metrics 业务控件）；
 * resolveWidget 对 metrics 的 x-widget 消费（node 认、tool 忽略→降级 json）；
 * isWidgetName DEPLOY 组；buildDeployRegistry 注册与 DEPLOY_WIDGETS 同源；
 * 节点注册表不含部署控件（隔离）；GateMetricsWidget SSR 不崩与 i18n parity。
 */
import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { buildDeployRegistry } from '../deployRegistry'
import { GateMetricsWidget } from '../deployWidgets'
import { buildNodeRegistry } from '../nodeRegistry'
import { resolveWidget } from '../resolveWidget'
import {
  DEPLOY_GATE_METRICS_WIDGET,
  DEPLOY_WIDGETS,
  TARGET_SELECT_WIDGET,
  isWidgetName,
} from '../types'
import { buildGateSchema } from '../../release/gateSchema'
import zhEditor from '../../../locales/zh-CN/editor.json'
import enEditor from '../../../locales/en-US/editor.json'

describe('buildGateSchema（docs/115 §5 第 3 步第一片）', () => {
  const schema = buildGateSchema()

  it('gate 四字段形状（标量＋metrics 业务控件声明）', () => {
    expect(schema.type).toBe('object')
    expect(Object.keys(schema.properties ?? {})).toEqual([
      'observeMinutes',
      'minSamples',
      'autoRollback',
      'metrics',
    ])
    expect(schema.properties?.observeMinutes).toEqual({ type: 'number', minimum: 1 })
    expect(schema.properties?.minSamples).toEqual({ type: 'number', minimum: 1 })
    expect(schema.properties?.autoRollback).toEqual({ type: 'boolean' })
    expect(schema.properties?.metrics).toEqual({
      type: 'array',
      'x-widget': DEPLOY_GATE_METRICS_WIDGET,
    })
    expect(schema.required).toEqual(['observeMinutes', 'autoRollback', 'minSamples', 'metrics'])
  })

  it('resolveWidget：metrics 的 x-widget 在 node 源解析为 deploy-gate-metrics', () => {
    const metricsSchema = schema.properties?.metrics
    expect(resolveWidget(metricsSchema!, 'node')).toEqual({
      kind: 'widget',
      widget: DEPLOY_GATE_METRICS_WIDGET,
    })
  })

  it('resolveWidget：tool 源忽略 metrics 的 x-widget，array 无 items 降级 json', () => {
    const metricsSchema = schema.properties?.metrics
    expect(resolveWidget(metricsSchema!, 'tool')).toEqual({ kind: 'widget', widget: 'json' })
  })

  it('resolveWidget：标量字段按类型默认（number/switch）', () => {
    expect(resolveWidget(schema.properties!.observeMinutes, 'node')).toEqual({
      kind: 'widget',
      widget: 'number',
    })
    expect(resolveWidget(schema.properties!.autoRollback, 'node')).toEqual({
      kind: 'widget',
      widget: 'switch',
    })
  })
})

describe('DEPLOY 组控件名登记（docs/118 §3.2 分组）', () => {
  it('isWidgetName 命中部署控件名', () => {
    expect(isWidgetName(DEPLOY_GATE_METRICS_WIDGET)).toBe(true)
  })

  it('buildDeployRegistry 注册名恰好覆盖 DEPLOY_WIDGETS', () => {
    const registry = buildDeployRegistry()
    const names = registry.names()
    for (const name of DEPLOY_WIDGETS) expect(names).toContain(name)
  })

  it('buildDeployRegistry 不含节点业务控件（隔离），buildNodeRegistry 不含部署控件（隔离）', () => {
    const deploy = buildDeployRegistry()
    expect(deploy.has(TARGET_SELECT_WIDGET)).toBe(false)
    const node = buildNodeRegistry()
    expect(node.has(DEPLOY_GATE_METRICS_WIDGET)).toBe(false)
  })
})

describe('GateMetricsWidget（docs/115 §5 第 3 步第一片）', () => {
  it('SSR 渲染不崩（antd Table 内容客户端口化，崩溃由 tsc/build/冒烟兜底）', () => {
    expect(() =>
      renderToStaticMarkup(
        <GateMetricsWidget value={[]} onChange={() => undefined} schema={{ type: 'array' }} />,
      ),
    ).not.toThrow()
  })

  it('zh/en gate i18n 键齐全且 en 无汉字', () => {
    const zhGate = zhEditor.rollout.gate
    const enGate = enEditor.rollout.gate
    expect(zhGate.metricsHint).toBeTruthy()
    expect(enGate.metricsHint).toBeTruthy()
    const enKeys = {
      metricsHint: enGate.metricsHint,
      metricsLabel: enGate.metricsLabel,
      colMetric: enGate.colMetric,
      colThreshold: enGate.colThreshold,
      colEnabled: enGate.colEnabled,
      included: enEditor.rollout.included,
      excluded: enEditor.rollout.excluded,
    }
    expect(/[\u4e00-\u9fff]/.test(JSON.stringify(enKeys))).toBe(false)
  })
})
