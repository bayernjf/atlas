import { useCallback, useEffect, useMemo, useState } from 'react'
import { Alert, Button, Spin, Tag, Tooltip } from 'antd'
import { useTranslation } from '../../locales'
import { getRunTrace, type TraceSpanNode } from '../../lib/apiClient'
import { formatDuration } from '../../lib/monitoring'
import {
  flattenSpans,
  formatSpanAttrs,
  layoutWaterfall,
  spanBarColor,
  spanKindColor,
  spanKindLabelKey,
} from '../../lib/traceTree'

// 模块级缓存：同一 run 折叠后再展开不重复拉取（v1 单页；刷新页面清空）。
const traceCache = new Map<string, TraceSpanNode | null>()

/** docs/33 §4.2：单运行 Trace 时间线瀑布（懒加载；历史/debug/回放无 spans 显空态）。 */
export function TraceWaterfall({ runId }: { runId: string }) {
  const { t } = useTranslation('monitoring')
  const cached = traceCache.get(runId)
  const [spans, setSpans] = useState<TraceSpanNode | null>(cached ?? null)
  const [loading, setLoading] = useState(() => !traceCache.has(runId))
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const trace = await getRunTrace(runId)
      traceCache.set(runId, trace.spans)
      setSpans(trace.spans)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setLoading(false)
    }
  }, [runId])

  // 首次拉取：setState 全部在 Promise 落定后（异步），避免 effect 内同步 setState 级联渲染。
  useEffect(() => {
    if (traceCache.has(runId)) return
    let cancelled = false
    getRunTrace(runId)
      .then((trace) => {
        if (cancelled) return
        traceCache.set(runId, trace.spans)
        setSpans(trace.spans)
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : String(err))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [runId])

  const laid = useMemo(
    () => (spans ? layoutWaterfall(flattenSpans(spans), spans) : []),
    [spans],
  )

  if (loading) {
    return (
      <div style={{ padding: 12 }}>
        <Spin size="small" />
      </div>
    )
  }
  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        style={{ margin: 8 }}
        message={t('trace.error')}
        action={
          <Button size="small" onClick={load}>
            {t('trace.retry')}
          </Button>
        }
      />
    )
  }
  if (!spans) {
    return <Alert type="info" showIcon style={{ margin: 8 }} message={t('trace.empty')} />
  }

  return (
    <div style={{ padding: '4px 8px' }}>
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '280px 1fr',
          color: '#999',
          fontSize: 12,
          borderBottom: '1px solid #f0f0f0',
          paddingBottom: 4,
          marginBottom: 4,
        }}
      >
        <span>{t('trace.col.name')}</span>
        <span>{t('trace.col.timeline')}</span>
      </div>
      {laid.map(({ span, depth, leftPct, widthPct }) => {
        const attrs = formatSpanAttrs(span)
        const label = (
          <span
            style={{
              marginLeft: depth * 16,
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              display: 'inline-block',
              maxWidth: '100%',
              color: span.status === 'error' ? '#cf1322' : undefined,
            }}
          >
            <Tag color={spanKindColor(span.kind)} style={{ marginInlineEnd: 6 }}>
              {t(spanKindLabelKey(span.kind))}
            </Tag>
            {span.name}
          </span>
        )
        return (
          <div
            key={span.spanId}
            style={{
              display: 'grid',
              gridTemplateColumns: '280px 1fr',
              alignItems: 'center',
              height: 26,
              fontSize: 12,
            }}
          >
            <div style={{ overflow: 'hidden', paddingRight: 8 }}>
              {attrs ? (
                <Tooltip title={<pre style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{attrs}</pre>}>
                  {label}
                </Tooltip>
              ) : (
                label
              )}
            </div>
            <div style={{ position: 'relative', height: 22 }}>
              <div
                style={{
                  position: 'absolute',
                  left: `${leftPct}%`,
                  width: `${widthPct}%`,
                  top: 4,
                  height: 14,
                  borderRadius: 3,
                  background: spanBarColor(span),
                }}
              />
              <span
                style={{
                  position: 'absolute',
                  left: `${Math.min(leftPct + widthPct + 0.6, 92)}%`,
                  top: 3,
                  color: '#888',
                  fontSize: 11,
                  whiteSpace: 'nowrap',
                }}
              >
                {formatDuration(span.durationMs)}
              </span>
            </div>
          </div>
        )
      })}
    </div>
  )
}
