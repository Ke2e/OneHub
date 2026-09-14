import { useCallback, useEffect, useState } from 'react'
import { channelApi } from '../api'
import type { Channel } from '../types'

const STATUS_MAP: Record<string, string> = {
  healthy: 'ok', manual_down: 'warn', down: 'err',
}
const BREAKER_MAP: Record<string, string> = {
  closed: 'ok', half_open: 'warn', open: 'err',
}

export default function Channels() {
  const [channels, setChannels] = useState<Channel[]>([])
  const [err, setErr] = useState('')
  const [editing, setEditing] = useState<Channel | null>(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({ name: '', provider: '', base_url: '', weight: 10, status: 'healthy' })

  const load = useCallback(async () => {
    setErr('')
    try {
      const res = await channelApi.list()
      setChannels(res.items)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : '加载失败')
    }
  }, [])

  useEffect(() => { load() }, [load])

  const openCreate = () => {
    setForm({ name: '', provider: '', base_url: '', weight: 10, status: 'healthy' })
    setEditing(null)
    setCreating(true)
  }
  const openEdit = (ch: Channel) => {
    setForm({ name: ch.name, provider: ch.provider, base_url: ch.base_url, weight: ch.weight, status: ch.status })
    setEditing(ch)
    setCreating(false)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    try {
      if (editing) await channelApi.update(editing.id, form)
      else await channelApi.create(form)
      setCreating(false); setEditing(null)
      await load()
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : '保存失败')
    }
  }

  const del = async (ch: Channel) => {
    if (!confirm(`删除渠道「${ch.name}」？被用量引用的渠道会被拒绝。`)) return
    try {
      await channelApi.remove(ch.id)
      await load()
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : '删除失败')
    }
  }

  const toggleStatus = async (ch: Channel) => {
    const next = ch.status === 'healthy' ? 'manual_down' : 'healthy'
    try {
      await channelApi.update(ch.id, { status: next })
      await load()
    } catch (ex) { setErr(ex instanceof Error ? ex.message : '更新失败') }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title">渠道管理</h1>
          <p className="page-sub">上游渠道 + 实时熔断三态可视化（<span className="mono">CircuitBreaker · Redis</span>）</p>
        </div>
        <button className="btn primary" onClick={openCreate}>+ 新增渠道</button>
      </div>

      {err && <div className="error-box">{err}</div>}

      {(creating || editing) && (
        <form className="card" onSubmit={save} style={{ marginBottom: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 12 }}>
            <div className="field">
              <label>名称 *</label>
              <input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
            </div>
            <div className="field">
              <label>Provider</label>
              <input value={form.provider} onChange={(e) => setForm({ ...form, provider: e.target.value })} placeholder="deepseek" />
            </div>
            <div className="field">
              <label>Base URL</label>
              <input value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} placeholder="https://api.deepseek.com/v1" />
            </div>
            <div className="field">
              <label>权重</label>
              <input type="number" value={form.weight} onChange={(e) => setForm({ ...form, weight: Number(e.target.value) })} />
            </div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button className="btn primary" type="submit">{editing ? '保存' : '创建'}</button>
            <button className="btn" type="button" onClick={() => { setCreating(false); setEditing(null) }}>取消</button>
          </div>
        </form>
      )}

      <div className="card">
        <table>
          <thead>
            <tr>
              <th>ID</th><th>名称</th><th>Provider</th><th>基础地址</th>
              <th>权重</th><th>渠道状态</th><th>熔断状态</th><th>失败数</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {channels.map((ch) => (
              <tr key={ch.id}>
                <td className="mono">{ch.id}</td>
                <td><strong>{ch.name}</strong></td>
                <td>{ch.provider}</td>
                <td className="mono" style={{ maxWidth: 220, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ch.base_url}</td>
                <td>{ch.weight}</td>
                <td><span className={`badge ${STATUS_MAP[ch.status] ?? 'info'}`}>{ch.status}</span></td>
                <td><span className={`badge ${BREAKER_MAP[ch.breaker_state?.state] ?? 'info'}`}>{ch.breaker_state?.state ?? 'closed'}</span></td>
                <td>{ch.breaker_state?.failure_count ?? ch.failure_count}</td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button className="btn small" onClick={() => openEdit(ch)}>编辑</button>{' '}
                  <button className="btn small" onClick={() => toggleStatus(ch)}>{ch.status === 'healthy' ? '停用' : '启用'}</button>{' '}
                  <button className="btn small danger" onClick={() => del(ch)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {channels.length === 0 && <div className="empty">暂无渠道</div>}
      </div>
    </div>
  )
}