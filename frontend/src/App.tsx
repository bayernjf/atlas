import { useState } from 'react'
import { ConfigProvider } from 'antd'
import { Dashboard } from './pages/Dashboard'
import { Editor } from './pages/Editor'
import { antdTheme } from './theme/tokens'

type Page = 'dashboard' | 'editor'

function App() {
  const [page, setPage] = useState<Page>('dashboard')

  return (
    <ConfigProvider theme={antdTheme}>
      {page === 'dashboard' ? (
        <Dashboard onOpenEditor={() => setPage('editor')} />
      ) : (
        <Editor />
      )}
    </ConfigProvider>
  )
}

export default App
