import type { ReactNode } from 'react'
import { App as AntdApp } from 'antd'

/**
 * 登录后的页面壳：只在这里挂 antd 的 App（message/notification 上下文）。
 *
 * 登录、邮件审批、网页嵌入三个无登录分支都不渲染 AntdApp，但入口曾静态引入它，
 * 把 antd 通知子树连同 ContextIsolator → form/trigger 一起拖进每个访客的首屏
 * （约 165 KB）。拆成独立 chunk 后，只有登录后的页面才按需下载。
 */
export function AuthenticatedShell({ children }: { children: ReactNode }) {
  return <AntdApp>{children}</AntdApp>
}
