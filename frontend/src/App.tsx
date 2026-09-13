import { useState } from 'react'
import { ConfigProvider } from 'antd'
import { Dashboard } from './pages/Dashboard'
import { Editor } from './pages/Editor'

type Page = 'dashboard' | 'editor'

function App() {
  const [page, setPage] = useState<Page>('dashboard')

  return (
    <ConfigProvider theme={{ token: { colorPrimary: '#1677ff' } }}>
      {page === 'dashboard' ? (
        <Dashboard onOpenEditor={() => setPage('editor')} />
      ) : (
        <Editor />
      )}
    </ConfigProvider>
  )
}

export default App
