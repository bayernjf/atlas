/**
 * 节点表单业务控件（M4，04 §4.10 扩展 / 03 `form_renderer`）。
 *
 * 节点 config 里「连线目标选择」（x-ref 字段，如 human_approval 的双目标、
 * loop 的循环体/退出目标）需要当前图的节点候选，不属于通用内置控件。本文件
 * 只放组件；控件名常量在 types.ts、注册表在 nodeRegistry.ts（保 fast-refresh
 * 单一导出：组件文件不混出函数/常量）。
 */
import { useEffect, useState } from 'react'
import { AutoComplete, Empty, Input, Select, Typography } from 'antd'
import {
  listCards,
  listGraphs,
  previewScheduleCron,
  type CardSummary,
  type CronPreview,
  type SavedGraphSummary,
} from '../apiClient'
import type { WidgetComponent } from './types'
import { DiagnosticText } from './widgets'
import { useTranslation } from '../../locales'

/**
 * 连线目标选择：值为目标节点 id；必填/悬空引用的错误由 L1/L2 诊断经
 * DiagnosticText 承接（与旧手写面板的 status:'error' 等价）。不允许清空
 * （对齐旧面板：目标字段必填，清空等同未选，应回到占位+报错态）。
 */
export const TargetSelectWidget: WidgetComponent = ({
  value,
  onChange,
  scope,
  diagnostics,
  placeholder,
}) => {
  const { t } = useTranslation('editor')
  const options = scope?.listNodeTargets?.() ?? []
  return (
    <>
      <Select
        showSearch
        allowClear={false}
        style={{ width: '100%' }}
        value={value ? String(value) : undefined}
        placeholder={placeholder ?? t('nodePicker.targetNode')}
        status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
        onChange={(next: string) => onChange(next)}
        options={options}
        optionFilterProp="label"
      />
      <DiagnosticText diagnostics={diagnostics} />
    </>
  )
}

/**
 * 已保存子图选择（subgraph.graphId，M4 批 2 ⑨）：挂载时拉一次 /api/graphs。
 * 空态引导先搭子流程并保存；加载失败显错误但不阻塞编辑（与旧手写 SubgraphConfig 等价）。
 * 必填/悬空由 L1 诊断经 DiagnosticText 承接。
 */
export const SavedGraphSelectWidget: WidgetComponent = ({
  value,
  onChange,
  diagnostics,
  placeholder,
}) => {
  const { t } = useTranslation('editor')
  const [graphs, setGraphs] = useState<SavedGraphSummary[]>([])
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    let cancelled = false
    listGraphs()
      .then((items) => {
        if (!cancelled) setGraphs(items)
      })
      .catch((error: Error) => {
        if (!cancelled) setLoadError(error.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (graphs.length === 0 && !loadError) {
    return (
      <>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t('nodePicker.savedGraphEmpty')}
        />
        <DiagnosticText diagnostics={diagnostics} />
      </>
    )
  }

  return (
    <>
      <Select
        showSearch
        allowClear={false}
        style={{ width: '100%' }}
        value={value ? String(value) : undefined}
        placeholder={placeholder ?? t('nodePicker.pickSavedGraph')}
        status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
        onChange={(next: string) => onChange(next)}
        optionFilterProp="label"
        options={graphs.map((item) => ({
          value: item.id,
          label: t('nodePicker.nodeCount', { id: item.id, count: item.node_count }),
        }))}
      />
      {loadError && <Typography.Text type="danger">{loadError}</Typography.Text>}
      <DiagnosticText diagnostics={diagnostics} />
    </>
  )
}

/**
 * 交互卡片选择（human_approval.cardTemplateId，M8）：挂载时拉一次 /api/cards。
 * 与子图选择不同，本字段可选：允许清空（allowClear），清空即回到 summary 旧路径；
 * 目录为空/加载失败也不阻塞编辑（该字段本就可留空）。
 */
export const CardSelectWidget: WidgetComponent = ({
  value,
  onChange,
  diagnostics,
  placeholder,
}) => {
  const { t } = useTranslation('editor')
  const [cards, setCards] = useState<CardSummary[]>([])
  const [loadError, setLoadError] = useState('')

  useEffect(() => {
    let cancelled = false
    listCards()
      .then((items) => {
        if (!cancelled) setCards(items)
      })
      .catch((error: Error) => {
        if (!cancelled) setLoadError(error.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (cards.length === 0 && !loadError) {
    return (
      <>
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t('nodePicker.builtinCardEmpty')}
        />
        <DiagnosticText diagnostics={diagnostics} />
      </>
    )
  }

  return (
    <>
      <Select
        showSearch
        allowClear
        style={{ width: '100%' }}
        value={value ? String(value) : undefined}
        placeholder={placeholder ?? t('nodePicker.pickCard')}
        status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
        onChange={(next: string | undefined) => onChange(next ?? '')}
        optionFilterProp="label"
        options={cards.map((item) => ({ value: item.id, label: `${item.name}（${item.id}）` }))}
      />
      {loadError && <Typography.Text type="danger">{loadError}</Typography.Text>}
      <DiagnosticText diagnostics={diagnostics} />
    </>
  )
}

/** 预演请求的防抖窗；输入节奏比它快时只发一次，避免每敲一个字符打一次后端。 */
const CRON_PREVIEW_DEBOUNCE_MS = 400

/** IANA 常用时区（timezone-input 下拉候选；自由输入仍允许任何 IANA 名）。 */
const COMMON_TIMEZONES = [
  'UTC',
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Asia/Singapore',
  'Asia/Kolkata',
  'Australia/Sydney',
  'Europe/London',
  'Europe/Berlin',
  'America/New_York',
  'America/Los_Angeles',
]

function tzClock(iso: string, timeZone?: string): string {
  // 槽位口径是 UTC；显示按调度的 IANA 时区转墙上时间（缺省/UTC＝旧行为逐字不变）。
  // Intl 失败（非法 tz）时退回 UTC 切片，读路径绝不抛（与后端 tz_of 兜底同纪律）。
  if (!timeZone || timeZone === 'UTC') return iso.slice(0, 16).replace('T', ' ')
  try {
    const parts = new Intl.DateTimeFormat('zh-CN', {
      timeZone,
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hour12: false,
    }).formatToParts(new Date(iso))
    const get = (type: string) => parts.find((p) => p.type === type)?.value ?? ''
    return `${get('year')}-${get('month')}-${get('day')} ${get('hour')}:${get('minute')}`
  } catch {
    return iso.slice(0, 16).replace('T', ' ')
  }
}

/**
 * Cron 表达式输入（trigger.schedule）：把后端唯一那份 cron 子集解释器借过来用，
 * 画布里就告诉运营"接下来三次几点响（UTC）"。
 *
 * 刻意**不在前端再写一个解析器**：两份实现对"日与周取并集""7＝周日"这类边角迟早分叉，
 * 分叉的表现就是这里绿、保存时 422。所以合法性与下次触发都问 `POST /api/schedules/cron-preview`
 * （理由与影响面记 docs/68 顶部落码偏差）。字段级不合法走这里的红字，不冒充 HTTP 错误。
 */
export const CronInputWidget: WidgetComponent = ({
  value,
  onChange,
  diagnostics,
  placeholder,
  timeZone,
}) => {
  const { t } = useTranslation('schedules')
  const cron = typeof value === 'string' ? value : ''
  const trimmed = cron.trim()
  /** 预演结果按"它属于哪个表达式"存着，显示与否靠派生——清空输入不会残留上一次的绿字。 */
  const [settled, setSettled] = useState<{ expression: string; preview: CronPreview | null } | null>(
    null,
  )
  const preview = settled?.expression === trimmed ? settled.preview : null
  const pending = trimmed !== '' && preview === null && settled?.expression !== trimmed

  useEffect(() => {
    if (!trimmed) return
    let cancelled = false
    const timer = setTimeout(() => {
      // 打包 ZL：预演带同 config 的 timezone（缺省 UTC），后端按该 tz 的墙上时间求值。
      previewScheduleCron(trimmed, timeZone ?? 'UTC')
        .then((result) => {
          if (!cancelled) setSettled({ expression: trimmed, preview: result })
        })
        .catch(() => {
          // 预演打不通（断网/未登录竞态）时不下任何结论：显式"问不出结果"，也不挡编辑。
          if (!cancelled) setSettled({ expression: trimmed, preview: null })
        })
    }, CRON_PREVIEW_DEBOUNCE_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [trimmed, timeZone])

  return (
    <>
      <Input
        value={cron}
        placeholder={placeholder ?? '0 9 * * *'}
        status={diagnostics?.some((d) => d.severity === 'error') ? 'error' : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {t('cron.hint')}
      </Typography.Text>
      {pending && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t('cron.previewing')}
        </Typography.Text>
      )}
      {!pending && preview?.valid && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t('cron.next', { times: preview.nextFireAt.map((iso) => tzClock(iso, timeZone)).join(' · ') })}
        </Typography.Text>
      )}
      {!pending && preview && !preview.valid && (
        <Typography.Text type="danger" style={{ fontSize: 12 }}>
          {t('cron.invalid', { message: preview.message })}
        </Typography.Text>
      )}
      {!pending && trimmed !== '' && preview === null && (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {t('cron.unavailable')}
        </Typography.Text>
      )}
      <DiagnosticText diagnostics={diagnostics} />
    </>
  )
}

/**
 * 时区输入（trigger.schedule 的 timezone 字段，打包 ZL）：IANA 常用区下拉＋自由输入。
 * 合法性在后端 DSL 保存期兜（NODE_TRIGGER_TZ_INVALID）；这里只做编辑与提示，不复制校验。
 */
export const TimezoneInputWidget: WidgetComponent = ({
  value,
  onChange,
  diagnostics,
  placeholder,
}) => {
  const { t } = useTranslation('schedules')
  const tz = typeof value === 'string' ? value : ''
  return (
    <>
      <AutoComplete
        value={tz}
        options={COMMON_TIMEZONES.map((zone) => ({ value: zone }))}
        placeholder={placeholder ?? 'Asia/Shanghai'}
        allowClear
        onChange={(next) => onChange(next)}
      />
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {t('cron.tzHint')}
      </Typography.Text>
      <DiagnosticText diagnostics={diagnostics} />
    </>
  )
}
