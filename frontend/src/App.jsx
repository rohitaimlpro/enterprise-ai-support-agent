import { useState } from 'react'
import './App.css'
import { AuthProvider, useAuth } from './context/AuthContext'
import LoginPage from './pages/LoginPage'
import ChatPage from './pages/ChatPage'
import EvalDashboardPage from './pages/EvalDashboardPage'

function AppShell() {
  const { token, loading } = useAuth()
  const [view, setView] = useState('chat') // 'chat' | 'eval'

  if (loading) {
    return (
      <div className="auth-page">
        <p>Loading...</p>
      </div>
    )
  }

  if (!token) {
    return <LoginPage />
  }

  if (view === 'eval') {
    return <EvalDashboardPage onBack={() => setView('chat')} />
  }

  return <ChatPage onShowEvalDashboard={() => setView('eval')} />
}

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  )
}
