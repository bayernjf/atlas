import { useEffect, useState } from 'react'
import { ConfigProvider } from 'antd'
import { Dashboard } from './pages/Dashboard'
import { Editor } from './pages/Editor'
import { Login } from './pages/Login'
import { Monitoring } from './pages/Monitoring'
import { Memory } from './pages/Memory'
import { Connections } from './pages/Connections'
import { Users } from './pages/Users'
import { Approvals } from './pages/Approvals'
import { EmailApproval } from './pages/EmailApproval'
import { logout as logoutApi } from './lib/apiClient'
import { getStoredPrincipal, roleCan, UNAUTHORIZED_EVENT, type Principal } from './lib/auth'
import { extractEmailToken } from './lib/approvals'
import { antdTheme } from './theme/tokens'

type Page = 'dashboard' | 'editor' | 'monitoring' | 'memory' | 'connections' | 'users' | 'approvals'

function App() {
  const emailToken = extractEmailToken(window.location.pathname)
  const [principal, setPrincipal] = useState<Principal | null>(() =>
    emailToken ? null : getStoredPrincipal(),
  )
  const [page, setPage] = useState<Page>('dashboard')

  useEffect(() => {
    const onUnauthorized = () => {
      setPrincipal(null)
      setPage('dashboard')
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [])

  // 邮件深链：登录恢复逻辑之后渲染无登录独立外壳（docs/36 §6）。
  if (emailToken) {
    return (
      <ConfigProvider theme={antdTheme}>
        <EmailApproval token={emailToken} />
      </ConfigProvider>
    )
  }

  async function handleLogout() {
    await logoutApi()
    setPrincipal(null)
    setPage('dashboard')
  }

  if (!principal) {
    return (
      <ConfigProvider theme={antdTheme}>
        <Login
          onLoggedIn={(loggedIn) => {
            setPrincipal(loggedIn)
            setPage('dashboard')
          }}
        />
      </ConfigProvider>
    )
  }

  return (
    <ConfigProvider theme={antdTheme}>
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
        />
      ) : page === 'approvals' ? (
        <Approvals
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
      ) : (
        <Editor principal={principal} onLogout={handleLogout} />
      )}
    </ConfigProvider>
  )
}

export default App
