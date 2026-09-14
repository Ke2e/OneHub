import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { authApi, setToken } from '../api'

export default function Login() {
  const nav = useNavigate()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [loading, setLoading] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr('')
    setLoading(true)
    try {
      const res = await authApi.login({ email, password })
      setToken(res.token)
      nav('/dashboard')
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : '登录失败')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-wrap">
      <form className="card login-card" onSubmit={submit}>
        <h1>OneHub 管理台</h1>
        <p className="hint">OpenAI 协议多模型聚合网关</p>
        {err && <div className="error-box">{err}</div>}
        <div className="field">
          <label>邮箱</label>
          <input type="email" value={email} onChange={(e) => setEmail(e.target.value)}
            placeholder="admin@onehub.dev" autoFocus required />
        </div>
        <div className="field">
          <label>密码</label>
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)}
            placeholder="••••••••" required />
        </div>
        <button className="btn primary" style={{ width: '100%' }} disabled={loading}>
          {loading ? '登录中…' : '登录'}
        </button>
      </form>
    </div>
  )
}