import { useCallback, useEffect, useState } from 'react'
import { channelApi, modelApi } from '../api'
import type { Channel, Model } from '../types'

export default function Models() {
  const [models, setModels] = useState<Model[]>([])
  const [channels, setChannels] = useState<Channel[]>([])
  const [err, setErr] = useState('')
  const [editing, setEditing] = useState<Model | null>(null)
  const [creating, setCreating] = useState(false)
  const [form, setForm] = useState({
    model_name: '', channel_id: '', input_price: '', output_price: '', enabled: true,
  })

  const load = useCallback(async () => {
    setErr('')
    try {
      const [r1, r2] = await Promise.all([modelApi.list(), channelApi.list()])
      setModels(r1.items)
      setChannels(r2.items)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : '加载失败')
    }
  }, [])

  useEffect(() => { load() }, [load])

  const channelName = (id: number | null) => {
    const c = channels.find((x) => x.id === id)
    return c ? c.name : id === null ? '—' : `#${id}`
  }

  const openCreate = () => {
    setForm({ model_name: '', channel_id: '', input_price: '', output_price: '', enabled: true })
    setEditing(null); setCreating(true)
  }
  const openEdit = (m: Model) => {
    setForm({
      model_name: m.model_name,
      channel_id: m.channel_id === null ? '' : String(m.channel_id),
      input_price: m.input_price === null ? '' : String(m.input_price),
      output_price: m.output_price === null ? '' : String(m.output_price),
      enabled: m.enabled,
    })
    setEditing(m); setCreating(false)
  }

  const save = async (e: React.FormEvent) => {
    e.preventDefault()
    const payload = {
      model_name: form.model_name,
      channel_id: form.channel_id === '' ? null : Number(form.channel_id),
      input_price: form.input_price === '' ? null : Number(form.input_price),
      output_price: form.output_price === '' ? null : Number(form.output_price),
      enabled: form.enabled,
    }
    try {
      if (editing) await modelApi.update(editing.id, payload)
      else await modelApi.create(payload)
      setCreating(false); setEditing(null)
      await load()
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : '保存失败')
    }
  }

  const del = async (m: Model) => {
    if (!confirm(`删除模型「${m.model_name}」？`)) return
    try {
      await modelApi.remove(m.id)
      await load()
    } catch (ex) { setErr(ex instanceof Error ? ex.message : '删除失败') }
  }

  const toggleEnabled = async (m: Model) => {
    try {
      await modelApi.update(m.id, { enabled: !m.enabled })
      await load()
    } catch (ex) { setErr(ex instanceof Error ? ex.message : '更新失败') }
  }

  return (
    <div>
      <div className="page-head">
        <div>
          <h1 className="page-title">模型定价</h1>
          <p className="page-sub">全局模型清单 + 按 token 计费 + 启用/停用</p>
        </div>
        <button className="btn primary" onClick={openCreate}>+ 新增模型</button>
      </div>

      {err && <div className="error-box">{err}</div>}

      {(creating || editing) && (
        <form className="card" onSubmit={save} style={{ marginBottom: 16 }}>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 12 }}>
            <div className="field">
              <label>模型名 *</label>
              <input value={form.model_name} onChange={(e) => setForm({ ...form, model_name: e.target.value })} required />
            </div>
            <div className="field">
              <label>渠道</label>
              <select value={form.channel_id} onChange={(e) => setForm({ ...form, channel_id: e.target.value })}>
                <option value="">（全局/不限）</option>
                {channels.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </select>
            </div>
            <div className="field">
              <label>输入价</label>
              <input type="number" step="any" value={form.input_price} onChange={(e) => setForm({ ...form, input_price: e.target.value })} placeholder="元/1k token" />
            </div>
            <div className="field">
              <label>输出价</label>
              <input type="number" step="any" value={form.output_price} onChange={(e) => setForm({ ...form, output_price: e.target.value })} placeholder="元/1k token" />
            </div>
            <div className="field">
              <label>启用</label>
              <select value={String(form.enabled)} onChange={(e) => setForm({ ...form, enabled: e.target.value === 'true' })}>
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
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
              <th>ID</th><th>模型名</th><th>渠道</th>
              <th>输入价</th><th>输出价</th><th>启用</th><th>操作</th>
            </tr>
          </thead>
          <tbody>
            {models.map((m) => (
              <tr key={m.id}>
                <td className="mono">{m.id}</td>
                <td className="mono">{m.model_name}</td>
                <td>{channelName(m.channel_id)}</td>
                <td>{m.input_price ?? '—'}</td>
                <td>{m.output_price ?? '—'}</td>
                <td>
                  <span className={`badge ${m.enabled ? 'ok' : 'warn'}`}>{m.enabled ? '启用' : '停用'}</span>
                </td>
                <td style={{ whiteSpace: 'nowrap' }}>
                  <button className="btn small" onClick={() => openEdit(m)}>编辑</button>{' '}
                  <button className="btn small" onClick={() => toggleEnabled(m)}>{m.enabled ? '停用' : '启用'}</button>{' '}
                  <button className="btn small danger" onClick={() => del(m)}>删除</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {models.length === 0 && <div className="empty">暂无模型</div>}
      </div>
    </div>
  )
}