import type { ReactNode } from 'react'
import { Alert, Empty, Spin } from 'antd'

/**
 * Unified loading / empty / error three-state component (UIUX P2 #8).
 *
 * Same props contract as the model-hub / ops-console AsyncState variants:
 * exactly one of loading / error / empty is shown, otherwise children render.
 * This atlas variant wraps antd primitives (Spin / Empty / Alert) so it fits
 * the existing antd-based frontend.
 */
export interface AsyncStateProps {
  loading?: boolean
  error?: string | null
  empty?: boolean
  loadingText?: string
  emptyText?: string
  errorText?: string
  /** Shown under the error message as an Alert action button. */
  onRetry?: () => void
  children?: ReactNode
}

export default function AsyncState({
  loading,
  error,
  empty,
  loadingText = '加载中…',
  emptyText = '暂无数据',
  errorText = '加载失败',
  onRetry,
  children,
}: AsyncStateProps) {
  if (loading) {
    return (
      <div style={{ padding: '24px 0', textAlign: 'center' }}>
        <Spin tip={loadingText} />
      </div>
    )
  }
  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message={`${errorText}：${error}`}
        action={
          onRetry ? (
            <button type="button" className="ant-btn ant-btn-primary" onClick={onRetry}>
              重试
            </button>
          ) : undefined
        }
      />
    )
  }
  if (empty) {
    return <Empty description={emptyText} />
  }
  return children
}
