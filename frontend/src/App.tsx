import { useEffect, useState } from 'react'
import { ConfigProvider, App as AntdApp } from 'antd'
import { Dashboard } from './pages/Dashboard'
import { Editor } from './pages/Editor'
import { Login } from './pages/Login'
import { Monitoring } from './pages/Monitoring'
import { Memory } from './pages/Memory'
import { Connections } from './pages/Connections'
import { Users } from './pages/Users'
import { Approvals } from './pages/Approvals'
import { AuditLog } from './pages/AuditLog'
import { OpenApiImports } from './pages/OpenApiImports'
import { EmailApproval } from './pages/EmailApproval'
import { Waits } from './pages/Waits'
import { Schedules } from './pages/Schedules'
import { Reflection } from './pages/Reflection'
import { Models } from './pages/Models'
import { MessageTemplates } from './pages/MessageTemplates'
import { logout as logoutApi } from './lib/apiClient'
import { ForcePasswordChange } from './components/ForcePasswordChange'
import {
  getStoredPrincipal,
  onPasswordChangeRequired,
  roleCan,
  UNAUTHORIZED_EVENT,
  type Principal,
} from './lib/auth'
import { extractEmailToken } from './lib/approvals'
import { antdTheme } from './theme/tokens'
import { useAntdLocale } from './locales/antdLocale'

type Page = 'dashboard' | 'editor' | 'monitoring' | 'memory' | 'connections' | 'users' | 'approvals' | 'audit' | 'openapi' | 'waits' | 'schedules' | 'reflection' | 'models' | 'templates'

function App() {
  const antdLocale = useAntdLocale()
  const emailToken = extractEmailToken(window.location.pathname)
  const [principal, setPrincipal] = useState<Principal | null>(() =>
    emailToken ? null : getStoredPrincipal(),
  )
  const [page, setPage] = useState<Page>('dashboard')
  // 打包 ZS（docs/92 E-3）：反思「去修改」跳转编辑器时待打开的图 id（App 状态导航，无 router）。
  const [editorGraphId, setEditorGraphId] = useState<string | null>(null)
  // 打包 ZU（docs/94 E-6）：节点级定位，与 editorGraphId 同置；无节点定位时为 null。
  const [editorNodeId, setEditorNodeId] = useState<string | null>(null)
  // 打包 AV（docs/95）：首登强制改密。状态**来自服务器**（登录响应／任一业务请求的 403 code），
  // 不在 localStorage 落一份——落了就会有一个会说谎的过期副本。
  const [mustChangePassword, setMustChangePassword] = useState(false)

  useEffect(() => {
    const onUnauthorized = () => {
      setPrincipal(null)
      setPage('dashboard')
      // 会话已经没了，强制改密框的表单不可能成功提交；回登录页而不是留一个死弹框。
      setMustChangePassword(false)
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    const unsubscribe = onPasswordChangeRequired(() => setMustChangePassword(true))
    return () => {
      window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
      unsubscribe()
    }
  }, [])

  // 邮件深链：登录恢复逻辑之后渲染无登录独立外壳（docs/36 §6）。
  if (emailToken) {
    return (
      <ConfigProvider theme={antdTheme} locale={antdLocale}>
        <EmailApproval token={emailToken} />
      </ConfigProvider>
    )
  }

  async function handleLogout() {
    await logoutApi()
    setPrincipal(null)
    setPage('dashboard')
    setMustChangePassword(false)
  }

  if (!principal) {
    return (
      <ConfigProvider theme={antdTheme} locale={antdLocale}>
        <Login
          onLoggedIn={(loggedIn) => {
            setPrincipal(loggedIn)
            setPage('dashboard')
          }}
        />
      </ConfigProvider>
    )
  }

  // 打包 AV（docs/95）：未满足首登强制改密时只挂这一层。不渲染页面壳是刻意的——壳里
  // 每个页面的首个请求都会撞 403，那不叫"被挡住"，叫一屏错误 toast。出口（改密端点）
  // 不经 require()，所以这个框永远不会把人关在平台外（U1143 钉住）。
  if (mustChangePassword) {
    return (
      <ConfigProvider theme={antdTheme} locale={antdLocale}>
        <div style={{ minHeight: '100vh', background: 'var(--atlas-color-bg-page)' }}>
          <ForcePasswordChange
            onDone={() => setMustChangePassword(false)}
            onLoggedOut={handleLogout}
          />
        </div>
      </ConfigProvider>
    )
  }

  return (
    <ConfigProvider theme={antdTheme} locale={antdLocale}>
      <AntdApp>
        {page === 'dashboard' ? (
        <Dashboard
          principal={principal}
          onLogout={handleLogout}
          onOpenEditor={() => setPage('editor')}
          onOpenMonitoring={() => setPage('monitoring')}
          onOpenMemory={() => setPage('memory')}
          onOpenConnections={() => setPage('connections')}
          onOpenUsers={() => setPage('users')}
          onOpenApprovals={() => setPage('approvals')}
          onOpenAudit={() => setPage('audit')}
          onOpenOpenApi={() => setPage('openapi')}
          onOpenWaits={() => setPage('waits')}
          onOpenSchedules={() => setPage('schedules')}
          onOpenReflection={() => setPage('reflection')}
          onOpenModels={() => setPage('models')}
          onOpenTemplates={() => setPage('templates')}
        />
      ) : page === 'approvals' ? (
        <Approvals
          principal={principal}
          onLogout={handleLogout}
          onBack={() => setPage('dashboard')}
        />
      ) : page === 'audit' && roleCan(principal.role, 'administer') ? (
        <AuditLog
          principal={principal}
          onLogout={handleLogout}
          onBack={() => setPage('dashboard')}
        />
      ) : page === 'monitoring' ? (
        <Monitoring principal={principal} onLogout={handleLogout} onBack={() => setPage('dashboard')} />
      ) : page === 'users' && roleCan(principal.role, 'administer') ? (
        <Users
          principal={principal}
          onLogout={handleLogout}
          onBack={() => setPage('dashboard')}
        />
      ) : page === 'memory' ? (
        <Memory principal={principal} onLogout={handleLogout} onBack={() => setPage('dashboard')} />
      ) : page === 'connections' && roleCan(principal.role, 'operate') ? (
        <Connections principal={principal} onLogout={handleLogout} onBack={() => setPage('dashboard')} />
      ) : page === 'waits' && roleCan(principal.role, 'operate') ? (
        <Waits
          principal={principal}
          onLogout={handleLogout}
          onBack={() => setPage('dashboard')}
        />
      ) : page === 'schedules' ? (
        // 列表是 read 面（viewer 也要能看见下次触发与跳过数），写操作在页内按角色禁用。
        <Schedules
          principal={principal}
          onLogout={handleLogout}
          onBack={() => setPage('dashboard')}
        />
      ) : page === 'reflection' ? (
        <Reflection
          principal={principal}
          onLogout={handleLogout}
          onBack={() => setPage('dashboard')}
          onOpenEditor={(graphId, nodeId) => {
            setEditorGraphId(graphId)
            setEditorNodeId(nodeId ?? null)
            setPage('editor')
          }}
          onOpenMonitoring={() => setPage('monitoring')}
        />
      ) : page === 'openapi' ? (
        <OpenApiImports principal={principal} onLogout={handleLogout} onBack={() => setPage('dashboard')} />
      ) : page === 'models' && roleCan(principal.role, 'read') ? (
        <Models principal={principal} onLogout={handleLogout} onBack={() => setPage('dashboard')} />
      ) : page === 'templates' ? (
        <MessageTemplates principal={principal} onBack={() => setPage('dashboard')} />
      ) : (
        <Editor
          principal={principal}
          onLogout={handleLogout}
          onBack={() => setPage('dashboard')}
          initialGraphId={editorGraphId}
          initialNodeId={editorNodeId}
        />
      )}
      </AntdApp>
    </ConfigProvider>
  )
}

export default App
