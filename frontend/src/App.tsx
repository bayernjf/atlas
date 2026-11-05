import { lazy, Suspense, useEffect, useState } from 'react'
import { ConfigProvider } from 'antd'
import { logout as logoutApi } from './lib/apiClient'
import {
  getStoredPrincipal,
  onPasswordChangeRequired,
  roleCan,
  UNAUTHORIZED_EVENT,
  type Principal,
} from './lib/auth'
import { extractEmailToken } from './lib/approvals'
import { extractEmbedToken } from './lib/embeds'
import { antdTheme } from './theme/tokens'
import { useAntdLocale } from './locales/antdLocale'

// P2-3：页面按需加载。登录后只进一个页面，没必要把 20 个页面（含 xyflow、
// 各页 antd 子树）一次塞进 entry chunk。命名导出用 .then 适配 lazy 的 default。
const Dashboard = lazy(() => import('./pages/Dashboard').then(m => ({ default: m.Dashboard })))
const Editor = lazy(() => import('./pages/Editor').then(m => ({ default: m.Editor })))
const Login = lazy(() => import('./pages/Login').then(m => ({ default: m.Login })))
const Monitoring = lazy(() => import('./pages/Monitoring').then(m => ({ default: m.Monitoring })))
const Memory = lazy(() => import('./pages/Memory').then(m => ({ default: m.Memory })))
const Connections = lazy(() => import('./pages/Connections').then(m => ({ default: m.Connections })))
const Users = lazy(() => import('./pages/Users').then(m => ({ default: m.Users })))
const Approvals = lazy(() => import('./pages/Approvals').then(m => ({ default: m.Approvals })))
const AuditLog = lazy(() => import('./pages/AuditLog').then(m => ({ default: m.AuditLog })))
const OpenApiImports = lazy(() => import('./pages/OpenApiImports').then(m => ({ default: m.OpenApiImports })))
const EmailApproval = lazy(() => import('./pages/EmailApproval').then(m => ({ default: m.EmailApproval })))
const Embed = lazy(() => import('./pages/Embed').then(m => ({ default: m.Embed })))
const EmbedsConfig = lazy(() => import('./pages/EmbedsConfig').then(m => ({ default: m.EmbedsConfig })))
const AdapterDrafts = lazy(() => import('./pages/AdapterDrafts').then(m => ({ default: m.AdapterDrafts })))
const Experiments = lazy(() => import('./pages/Experiments').then(m => ({ default: m.Experiments })))
const Waits = lazy(() => import('./pages/Waits').then(m => ({ default: m.Waits })))
const Schedules = lazy(() => import('./pages/Schedules').then(m => ({ default: m.Schedules })))
const Agents = lazy(() => import('./pages/Agents').then(m => ({ default: m.Agents })))
const Reflection = lazy(() => import('./pages/Reflection').then(m => ({ default: m.Reflection })))
const Models = lazy(() => import('./pages/Models').then(m => ({ default: m.Models })))
const Usage = lazy(() => import('./pages/Usage').then(m => ({ default: m.Usage })))
const MessageTemplates = lazy(() => import('./pages/MessageTemplates').then(m => ({ default: m.MessageTemplates })))
const ForcePasswordChange = lazy(() =>
  import('./components/ForcePasswordChange').then(m => ({ default: m.ForcePasswordChange })),
)
// antd App（message/notification 上下文）只在登录后的页面壳里用到，拆成独立 chunk，
// 不随入口进入登录/邮件/嵌入分支的首屏。
const AuthenticatedShell = lazy(() =>
  import('./components/AuthenticatedShell').then(m => ({ default: m.AuthenticatedShell }),
  ),
)

type Page = 'dashboard' | 'editor' | 'monitoring' | 'memory' | 'connections' | 'users' | 'approvals' | 'audit' | 'openapi' | 'waits' | 'schedules' | 'agents' | 'reflection' | 'models' | 'usage' | 'templates' | 'embeds' | 'adapterDrafts' | 'experiments'

/** 页面 chunk 下载完成前的占位；每个 lazy 子树都必须挂在 Suspense 下。 */
function PageFallback() {
  return (
    <div
      style={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        color: 'var(--atlas-color-text-secondary)',
      }}
    >
      Loading…
    </div>
  )
}

function App() {
  const antdLocale = useAntdLocale()
  const emailToken = extractEmailToken(window.location.pathname)
  // 打包 BQ（docs/133 D76）：网页嵌入深链 /embed/{token}，同样无登录独立外壳。
  const embedToken = extractEmbedToken(window.location.pathname)
  const [principal, setPrincipal] = useState<Principal | null>(() =>
    emailToken || embedToken ? null : getStoredPrincipal(),
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
        <Suspense fallback={<PageFallback />}>
          <EmailApproval token={emailToken} />
        </Suspense>
      </ConfigProvider>
    )
  }

  // 网页嵌入深链：/embed/{token} 公开只读面（docs/133 D76），无登录态。
  if (embedToken) {
    return (
      <ConfigProvider theme={antdTheme} locale={antdLocale}>
        <Suspense fallback={<PageFallback />}>
          <Embed token={embedToken} />
        </Suspense>
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
        <Suspense fallback={<PageFallback />}>
          <Login
            onLoggedIn={(loggedIn) => {
              setPrincipal(loggedIn)
              setPage('dashboard')
            }}
          />
        </Suspense>
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
          <Suspense fallback={<PageFallback />}>
            <ForcePasswordChange
              onDone={() => setMustChangePassword(false)}
              onLoggedOut={handleLogout}
            />
          </Suspense>
        </div>
      </ConfigProvider>
    )
  }

  return (
    <ConfigProvider theme={antdTheme} locale={antdLocale}>
      <Suspense fallback={<PageFallback />}>
        <AuthenticatedShell>
        <Suspense fallback={<PageFallback />}>
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
          onOpenAgents={() => setPage('agents')}
          onOpenReflection={() => setPage('reflection')}
          onOpenModels={() => setPage('models')}
          onOpenUsage={() => setPage('usage')}
          onOpenTemplates={() => setPage('templates')}
          onOpenEmbeds={() => setPage('embeds')}
          onOpenAdapterDrafts={() => setPage('adapterDrafts')}
          onOpenExperiments={() => setPage('experiments')}
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
      ) : page === 'agents' ? (
        // 与 schedules 同款纪律：列表 read 面可见，写操作页内按角色禁用（docs/131 D74）。
        <Agents
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
      ) : page === 'embeds' && roleCan(principal.role, 'administer') ? (
        <EmbedsConfig principal={principal} onLogout={handleLogout} onBack={() => setPage('dashboard')} />
      ) : page === 'adapterDrafts' ? (
        // 列表是 read 面（viewer 也可见草稿与决策状态）；写操作页内按角色禁用（docs/133 D71）。
        <AdapterDrafts principal={principal} onLogout={handleLogout} onBack={() => setPage('dashboard')} />
      ) : page === 'experiments' ? (
        // 列表是 read 面（viewer 也可见实验与判定状态）；创建/收结在页内按角色禁用（docs/134 BR-D4）。
        <Experiments principal={principal} onLogout={handleLogout} onBack={() => setPage('dashboard')} />
      ) : page === 'models' && roleCan(principal.role, 'read') ? (
        <Models principal={principal} onLogout={handleLogout} onBack={() => setPage('dashboard')} />
      ) : page === 'usage' && roleCan(principal.role, 'read') ? (
        <Usage principal={principal} onLogout={handleLogout} onBack={() => setPage('dashboard')} />
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
        </Suspense>
        </AuthenticatedShell>
      </Suspense>
    </ConfigProvider>
  )
}

export default App
