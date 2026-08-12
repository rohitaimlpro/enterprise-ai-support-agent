import { useState } from 'react'
import './App.css'
import { AuthProvider, useAuth } from './context/AuthContext'
import LoginPage from './pages/LoginPage'
import ChatPage from './pages/ChatPage'
import EvalDashboardPage from './pages/EvalDashboardPage'
import AdminTracePage from './pages/AdminTracePage'

function AppShell() {
  const { token, loading, user } = useAuth()
  const [view, setView] = useState('chat') // 'chat' | 'eval' | 'traces'

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

  if (view === 'traces' && user?.is_admin) {
    return <AdminTracePage onBack={() => setView('chat')} />
  }

  return (
    <ChatPage
      onShowEvalDashboard={() => setView('eval')}
      onShowAdminTraces={user?.is_admin ? () => setView('traces') : null}
    />
  )
}

export default function App() {
  return (
    <AuthProvider>
      <AppShell />
    </AuthProvider>
  )
}
