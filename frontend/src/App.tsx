import { Navigate, NavLink, Route, Routes, useNavigate } from 'react-router-dom'
import { clearToken, getToken } from './api'
import Dashboard from './pages/Dashboard'
import Channels from './pages/Channels'
import Models from './pages/Models'
import Keys from './pages/Keys'
import Playground from './pages/Playground'
import Login from './pages/Login'
import './app.css'

function Layout() {
  const nav = useNavigate()
  const logout = () => {
    clearToken()
    nav('/login')
  }
  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          <strong>OneHub</strong>
          <span>管理台</span>
        </div>
        <nav>
          <NavLink to="/dashboard">仪表盘</NavLink>
          <NavLink to="/channels">渠道</NavLink>
          <NavLink to="/models">模型定价</NavLink>
          <NavLink to="/keys">API Keys</NavLink>
          <NavLink to="/playground">Playground</NavLink>
        </nav>
        <button className="btn logout" onClick={logout}>退出登录</button>
      </aside>
      <main className="content">
        <Routes>
          <Route path="/dashboard" element={<Dashboard />} />
          <Route path="/channels" element={<Channels />} />
          <Route path="/models" element={<Models />} />
          <Route path="/keys" element={<Keys />} />
          <Route path="/playground" element={<Playground />} />
          <Route path="*" element={<Navigate to="/dashboard" replace />} />
        </Routes>
      </main>
    </div>
  )
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  if (!getToken()) return <Navigate to="/login" replace />
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route
        path="/*"
        element={
          <RequireAuth>
            <Layout />
          </RequireAuth>
        }
      />
    </Routes>
  )
}