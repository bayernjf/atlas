import { useState } from 'react'
import { ConfigProvider } from 'antd'
import { Dashboard } from './pages/Dashboard'
import { Editor } from './pages/Editor'
import { Monitoring } from './pages/Monitoring'
import { antdTheme } from './theme/tokens'

type Page = 'dashboard' | 'editor' | 'monitoring'

function App() {
  const [page, setPage] = useState<Page>('dashboard')

  return (
    <ConfigProvider theme={antdTheme}>
      {page === 'dashboard' ? (
        <Dashboard
          onOpenEditor={() => setPage('editor')}
          onOpenMonitoring={() => setPage('monitoring')}
        />
      ) : page === 'monitoring' ? (
        <Monitoring onBack={() => setPage('dashboard')} />
      ) : (
        <Editor />
      )}
    </ConfigProvider>
  )
}

export default App
