import { useCallback, useEffect, useState } from 'react'
import { keyApi } from '../api'
import type { ApiKey } from '../types'

export default function Keys() {
  const [keys, setKeys] = useState<ApiKey[]>([])
  const [err, setErr] = useState('')
  const [creating, setCreating] = useState(false)
  const [plaintext, setPlaintext] = useState('')
  const [form, setForm] = useState({ name: '', whitelist: '' })

  const load = useCallback(async () => {
    setErr('')
    try {
      const res = await keyApi.list()
      setKeys(res.items)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : '加载失败')
    }
  }, [])

  useEffect(() => { load() }, [load])

  const create = async (e: React.FormEvent) => {
    e.preventDefault()
    setErr('')
    setPlaintext('')
    const whitelist = form.whitelist.split(/[\s,，]+/).filter(Boolean)
    try {
      const res = await keyApi.create({
        name: form.name || undefined,
        model_whitelist: whitelist.length ? whitelist : undefined,
      })
      setPlaintext(res.key ?? '')
      setForm({ name: '', whitelist: '' })
      setCreating(false)
      await load()
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : '创建失败')
    }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title">API Keys</h1>
          <p className="page-sub">SK-Key 管理：哈希入库，明文仅创建时返回一次</p>
        </div>
        <button className="btn primary" onClick={() => { setCreating(true); setPlaintext('') }}>+ 新建 Key</button>
      </div>

      {err && <div className="error-box">{err}</div>}

      {plaintext && (
        <div className="card" style={{ marginBottom: 16, borderColor: 'var(--brand)', background: 'var(--brand-soft)' }}>
          <strong>Key 已创建（只显示这一次，请立即保存）：</strong>
          <div className="mono" style={{ marginTop: 8, userSelect: 'all', wordBreak: 'break-all' }}>{plaintext}</div>
        </div>
      )}

      {creating && (
        <form className="card" onSubmit={create} style={{ marginBottom: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12 }}>
            <div className="field">
              <label>名称（选填）</label>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="我的 Key" />
            </div>
            <div className="field">
              <label>模型白名单（选填，逗号/空格分隔）</label>
              <input value={form.whitelist} onChange={(e) => setForm({ ...form, whitelist: e.target.value })} placeholder="deepseek-v4-flash-0731" />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn primary" type="submit">创建</button>
            <button className="btn" type="button" onClick={() => setCreating(false)}>取消</button>
          </div>
        </form>
      )}

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>ID</th><th>名称</th><th>前缀</th><th>白名单</th>
              <th>RPM</th><th>TPM</th><th>状态</th><th>创建时间</th>
            </tr>
          </thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k.id}>
                <td className="mono">{k.id}</td>
                <td>{k.name || '—'}</td>
                <td className="mono">{k.key_prefix || '—'}</td>
                <td className="mono" style={{ maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {k.model_whitelist?.length ? k.model_whitelist.join(', ') : '（全部）'}
                </td>
                <td>{k.rpm_limit ?? '∞'}</td>
                <td>{k.tpm_limit ?? '∞'}</td>
                <td><span className={`badge ${k.status === 'active' ? 'ok' : 'warn'}`}>{k.status}</span></td>
                <td>{k.created_at ? new Date(k.created_at).toLocaleString() : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {keys.length === 0 && <div className="empty">暂无 Key</div>}
      </div>
    </div>
  )
}