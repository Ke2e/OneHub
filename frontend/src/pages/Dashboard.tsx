import { useCallback, useEffect, useRef, useState } from 'react'
import * as echarts from 'echarts'
import { dashboardApi } from '../api'
import type { ChannelStat, Overview, UsageLog } from '../types'

function Kpi({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="card kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  )
}

export default function Dashboard() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [channelStats, setChannelStats] = useState<ChannelStat[]>([])
  const [logs, setLogs] = useState<UsageLog[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [err, setErr] = useState('')
  const pieRef = useRef<HTMLDivElement>(null)
  const barRef = useRef<HTMLDivElement>(null)
  const pieCh = useRef<echarts.ECharts | null>(null)
  const barCh = useRef<echarts.ECharts | null>(null)

  const load = useCallback(async () => {
    setErr('')
    try {
      const [o, c, l] = await Promise.all([
        dashboardApi.overview(),
        dashboardApi.channels(),
        dashboardApi.logs({ offset: page * 10, limit: 10 }),
      ])
      setOverview(o)
      setChannelStats(c.items)
      setLogs(l.items)
      setTotal(l.total)
    } catch (ex) {
      setErr(ex instanceof Error ? ex.message : '加载失败')
    }
  }, [page])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    if (!pieRef.current || !barRef.current || channelStats.length === 0) return
    pieCh.current = echarts.init(pieRef.current)
    barCh.current = echarts.init(barRef.current)

    pieCh.current.setOption({
      tooltip: { trigger: 'item', formatter: '{b}: {c} 次 ({d}%)' },
      series: [{
        type: 'pie', radius: ['42%', '68%'],
        data: channelStats.map((s) => ({ name: `渠道 #${s.channel_id}`, value: s.requests })),
        label: { formatter: '{b}: {c}' },
      }],
    })

    barCh.current.setOption({
      tooltip: { trigger: 'axis' },
      xAxis: { type: 'category', data: channelStats.map((s) => `#${s.channel_id}`) },
      yAxis: { type: 'value' },
      series: [{
        name: 'Token', type: 'bar',
        data: channelStats.map((s) => s.tokens),
        itemStyle: { color: '#2563eb' },
      }],
    })

    const onResize = () => { pieCh.current?.resize(); barCh.current?.resize() }
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      pieCh.current?.dispose(); barCh.current?.dispose()
      pieCh.current = null; barCh.current = null
    }
  }, [channelStats])

  return (
    <div>
      <h1 className="page-title">仪表盘</h1>
      <p className="page-sub">实时用量 · token · 成本 · 渠道分流（ECharts）</p>

      {err && <div className="error-box">{err}</div>}

      <div className="kpi-grid">
        <Kpi label="24h 请求量" value={String(overview?.requests_24h ?? 0)}
          sub={`累计 ${overview?.total_requests ?? 0}`} />
        <Kpi label="24h Token" value={String(overview?.tokens_24h ?? 0)}
          sub={`累计 ${overview?.total_tokens ?? 0}`} />
        <Kpi label="24h 成本 (¥)" value={(overview?.cost_24h ?? 0).toFixed(4)}
          sub={`累计 ¥${(overview?.total_cost ?? 0).toFixed(4)}`} />
        <Kpi label="活跃渠道" value={`${overview?.active_channels ?? 0}/${overview?.channel_count ?? 0}`} />
      </div>

      <div className="chart-grid">
        <div className="card"><div className="kpi-label">渠道分流（请求数）</div><div ref={pieRef} style={{ height: 280 }} /></div>
        <div className="card"><div className="kpi-label">各渠道 Token 用量</div><div ref={barRef} style={{ height: 280 }} /></div>
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <div className="kpi-label" style={{ marginBottom: 8 }}>用量明细</div>
        <table>
          <thead>
            <tr>
              <th>模型</th><th>渠道</th><th>Tokens</th><th>延迟(ms)</th>
              <th>状态码</th><th>成本(¥)</th><th>时间</th>
            </tr>
          </thead>
          <tbody>
            {logs.map((l) => (
              <tr key={l.id}>
                <td className="mono">{l.model}</td>
                <td>{l.channel_id ?? '—'}</td>
                <td>{(l.prompt_tokens ?? 0) + (l.completion_tokens ?? 0)}</td>
                <td>{l.latency_ms ?? '—'}</td>
                <td><span className={`badge ${l.status_code >= 200 && l.status_code < 300 ? 'ok' : 'err'}`}>{l.status_code}</span></td>
                <td>{(l.cost ?? 0).toFixed(4)}</td>
                <td>{l.created_at ? new Date(l.created_at).toLocaleString() : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {logs.length === 0 && <div className="empty">暂无用量记录</div>}
        <div className="pager">
          <button className="btn small" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>上一页</button>
          <span>第 {page + 1} 页 / 共 {Math.max(1, Math.ceil(total / 10))} 页（{total} 条）</span>
          <button className="btn small" disabled={(page + 1) * 10 >= total} onClick={() => setPage((p) => p + 1)}>下一页</button>
        </div>
      </div>
    </div>
  )
}