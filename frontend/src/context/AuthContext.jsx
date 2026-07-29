import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { apiFetch, clearToken, getToken, setToken as persistToken } from '../api/client'

const AuthContext = createContext(null)

export function AuthProvider({ children }) {
  const [token, setToken] = useState(() => getToken())
  const [user, setUser] = useState(null)
  const [loading, setLoading] = useState(Boolean(getToken()))

  useEffect(() => {
    if (!token) {
      setLoading(false)
      return
    }
    apiFetch('/auth/me', { auth: true })
      .then(setUser)
      .catch(() => {
        // Token expired or invalid -- drop it and fall back to the login screen.
        clearToken()
        setToken(null)
      })
      .finally(() => setLoading(false))
  }, [token])

  const login = useCallback(async (email, password) => {
    const data = await apiFetch('/auth/login', { method: 'POST', body: { email, password } })
    persistToken(data.access_token)
    setToken(data.access_token)
  }, [])

  const register = useCallback(async (email, password, fullName) => {
    const data = await apiFetch('/auth/register', {
      method: 'POST',
      body: { email, password, full_name: fullName },
    })
    persistToken(data.access_token)
    setToken(data.access_token)
  }, [])

  const logout = useCallback(() => {
    clearToken()
    setToken(null)
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ token, user, loading, login, register, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used inside <AuthProvider>')
  return ctx
}
