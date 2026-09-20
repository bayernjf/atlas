import { useEffect, useState } from 'react'
import { ConfigProvider } from 'antd'
import { Dashboard } from './pages/Dashboard'
import { Editor } from './pages/Editor'
import { Login } from './pages/Login'
import { Monitoring } from './pages/Monitoring'
import { Memory } from './pages/Memory'
import { logout as logoutApi } from './lib/apiClient'
import { getStoredPrincipal, UNAUTHORIZED_EVENT, type Principal } from './lib/auth'
import { antdTheme } from './theme/tokens'

type Page = 'dashboard' | 'editor' | 'monitoring' | 'memory'

function App() {
  const [principal, setPrincipal] = useState<Principal | null>(() => getStoredPrincipal())
  const [page, setPage] = useState<Page>('dashboard')

  useEffect(() => {
    const onUnauthorized = () => {
      setPrincipal(null)
      setPage('dashboard')
    }
    window.addEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onUnauthorized)
  }, [])

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
        />
      ) : page === 'monitoring' ? (
        <Monitoring principal={principal} onLogout={handleLogout} onBack={() => setPage('dashboard')} />
      ) : page === 'memory' ? (
        <Memory principal={principal} onLogout={handleLogout} onBack={() => setPage('dashboard')} />
      ) : (
        <Editor principal={principal} onLogout={handleLogout} />
      )}
    </ConfigProvider>
  )
}

export default App
