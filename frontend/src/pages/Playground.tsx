import { useEffect, useState } from 'react'
import { modelApi, playApi } from '../api'
import type { ChatMessage, Model } from '../types'

export default function Playground() {
  const [models, setModels] = useState<Model[]>([])
  const [selected, setSelected] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [streaming, setStreaming] = useState(false)

  useEffect(() => {
    modelApi.list().then((r) => {
      setModels(r.items.filter((m) => m.enabled))
      if (r.items.some((m) => m.enabled)) setSelected((s) => s || r.items.find((m) => m.enabled)!.model_name)
    }).catch(() => {})
  }, [])

  const send = async () => {
    const text = input.trim()
    if (!text || busy || !selected) return
    const userMsg: ChatMessage = { role: 'user', content: text }
    const next: ChatMessage[] = [...messages, userMsg]
    setMessages(next)
    setInput('')
    setBusy(true)
    setErr('')
    setStreaming(streaming)

    try {
      const resp = await playApi.chat({ model: selected, messages: next, stream: streaming })
      if (!resp.ok) {
        const payload = await resp.json().catch(() => ({}))
        throw new Error(payload?.error?.message || `上游错误(${resp.status})`)
      }
      const contentType = resp.headers.get('content-type') || ''
      if (contentType.includes('event-stream')) {
        let buffer = ''
        const decoder = new TextDecoder()
        const reader = resp.body!.getReader()
        let acc = ''
        while (true) {
          const { done, value } = await reader.read()
          if (done) break
          acc += decoder.decode(value, { stream: true })
          const lines = acc.split('\n')
          acc = lines.pop() || ''
          for (const line of lines) {
            if (!line.startsWith('data:')) continue
            const data = line.slice(5).trim()
            if (data === '[DONE]') continue
            try {
              const chunk = JSON.parse(data)
              const delta = chunk?.choices?.[0]?.delta?.content
              if (delta) buffer += delta
            } catch { /* 忽略解析失败的 chunk */ }
          }
        }
        setMessages((m) => [...m, { role: 'assistant', content: buffer }])
      } else {
        const data = await resp.json()
        const content = data?.choices?.[0]?.message?.content ?? ''
        setMessages((m) => [...m, { role: 'assistant', content }])
      }
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : '请求失败')
    } finally {
      setBusy(false)
    }
  }

  const clear = () => { setMessages([]); setErr('') }

  return (
    <div>
      <h1 className="page-title">Playground</h1>
      <p className="page-sub">JWT 鉴权 → 经智能路由发往上游（流式/非流式）</p>

      <div className="card" style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 14 }}>
        <label style={{ color: 'var(--text-2)' }}>模型</label>
        <select value={selected} onChange={(e) => setSelected(e.target.value)} style={{ flex: 1 }}>
          {models.map((m) => <option key={m.id} value={m.model_name}>{m.model_name}</option>)}
        </select>
        <label style={{ display: 'flex', gap: 6, alignItems: 'center', color: 'var(--text-2)' }}>
          <input type="checkbox" checked={streaming} onChange={(e) => setStreaming(e.target.checked)} />
          流式
        </label>
        <button className="btn" onClick={clear}>清空</button>
      </div>

      {err && <div className="error-box">{err}</div>}

      <div className="card chat" style={{ marginBottom: 14 }}>
        {messages.length === 0 && <div className="empty">发送一条消息开始对话</div>}
        {messages.map((m, i) => (
          <div key={i} className={`msg ${m.role}`}>
            <div className="msg-role">{m.role === 'user' ? '你' : '助理'}</div>
            <div className="msg-content" style={{ whiteSpace: 'pre-wrap' }}>{m.content}</div>
          </div>
        ))}
      </div>

      <div className="card" style={{ display: 'flex', gap: 8 }}>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send() } }}
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          rows={2}
          style={{ flex: 1, resize: 'vertical' }}
        />
        <button className="btn primary" onClick={send} disabled={busy || !input.trim()}>
          {busy ? '…' : '发送'}
        </button>
      </div>
    </div>
  )
}