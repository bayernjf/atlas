/**
 * 部署面业务控件（docs/115 §5 第 3 步第一片，2026-10-10）。
 *
 * GateMetricsWidget：gate.metrics「固定集合成员的可空阈值」编辑——三行固定
 * GATE_METRIC_SPECS，每行一个 0–1 阈值输入（缺省显示 defaultThreshold、未启用
 * 显示 excluded），启用由 metrics 数组是否含该 id 决定（写阈值即启用）。
 * 受控单一（docs/118 §3.3）：全部编辑结果经 onChange 交回整个 metrics 数组。
 *
 * 形状契约（docs/118 §3.1）：x-widget 名 deploy-gate-metrics（<domain>-<semantic>），
 * 组件函数、入参 WidgetProps（value/onChange 即 metrics 数组）、返回 ReactNode。
 */
import { InputNumber, Table, Tag, Typography } from 'antd'
import { useTranslation } from '../../locales'
import { GATE_METRIC_SPECS, upsertGateMetric } from '../release'
import type { GateMetric } from '../apiClient'
import type { WidgetComponent } from './types'

const { Text } = Typography

export const GateMetricsWidget: WidgetComponent = ({ value, onChange }) => {
  const { t } = useTranslation('editor')
  const metrics: GateMetric[] = Array.isArray(value) ? (value as GateMetric[]) : []
  return (
    <div>
      <Table
        size="small"
        pagination={false}
        rowKey="id"
        dataSource={GATE_METRIC_SPECS.map((spec) => ({
          ...spec,
          metric: metrics.find((item) => item.id === spec.id),
        }))}
        columns={[
          { title: t('rollout.gate.colMetric'), dataIndex: 'label', render: (label: string) => t(label) },
          {
            title: t('rollout.gate.colThreshold'),
            dataIndex: 'metric',
            render: (metric: GateMetric | undefined, row: (typeof GATE_METRIC_SPECS)[number]) => (
              <InputNumber
                style={{ width: 120 }}
                min={0}
                max={1}
                step={row.step}
                value={metric?.threshold ?? row.defaultThreshold}
                onChange={(next) =>
                  next !== null &&
                  onChange(
                    upsertGateMetric(metrics, {
                      id: row.id,
                      threshold: next,
                      compareWith: metric?.compareWith ?? null,
                    }),
                  )
                }
              />
            ),
          },
          {
            title: t('rollout.gate.colEnabled'),
            dataIndex: 'metric',
            render: (metric: GateMetric | undefined) => (
              <Tag color={metric ? 'green' : 'default'}>
                {metric ? t('rollout.included') : t('rollout.excluded')}
              </Tag>
            ),
          },
        ]}
      />
      <Text type="secondary" style={{ display: 'block', marginTop: 4 }}>
        {t('rollout.gate.metricsHint')}
      </Text>
    </div>
  )
}
