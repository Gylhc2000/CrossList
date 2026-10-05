'use client'

import { useCallback, useEffect, useState } from 'react'
import { ApiError, api } from '@/lib/api'
import { Empty, Icon, Modal, Skeleton } from '@/components/ui'
import type { HistoryJob, UserMe } from '@/lib/types'

const STATUS: Record<HistoryJob['status'], { label: string; cls: string }> = {
  done: { label: '已完成', cls: 'badge-ok' },
  running: { label: '进行中', cls: 'badge-brand' },
  queued: { label: '排队中', cls: 'badge-neutral' },
  error: { label: '失败', cls: 'badge-err' },
  cancelled: { label: '已取消', cls: 'badge-neutral' },
}

function relTime(ts: number) {
  // 服务端的时间戳是**秒**（time.time()），Date.now() 是毫秒：
  // 不换算会算出「20710 天前」这种 1970 年的相对时间
  const d = Math.floor(Date.now() / 1000 - ts)
  if (d < 60) return '刚刚'
  if (d < 3600) return `${Math.floor(d / 60)} 分钟前`
  if (d < 86400) return `${Math.floor(d / 3600)} 小时前`
  return `${Math.floor(d / 86400)} 天前`
}

interface Props {
  open: boolean
  onClose: () => void
  onPick: (job: HistoryJob) => void
  me: UserMe | null
  onChanged?: () => void
}

/**
 * 我的生成记录：数据来自后端按账号过滤的历史表，所以换浏览器、
 * 服务重启后仍然在。产物文件另有 TTL，过期条目仍可见但下载会失效。
 */
export default function HistoryModal({ open, onClose, onPick, me, onChanged }: Props) {
  const [list, setList] = useState<HistoryJob[]>([])
  const [state, setState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [err, setErr] = useState('')
  const [pendingDel, setPendingDel] = useState('')

  const load = useCallback(async () => {
    setState('loading')
    setErr('')
    try {
      const d = await api.myJobs(60)
      setList(d.jobs ?? [])
      setState('ready')
    } catch (e) {
      setErr((e as ApiError)?.message || '读取历史记录失败')
      setState('error')
    }
  }, [])

  useEffect(() => {
    if (open) void load()
  }, [open, load])

  const remove = async (job: HistoryJob) => {
    if (pendingDel && pendingDel !== job.jobId) {
      setPendingDel(job.jobId)
      return
    }
    if (pendingDel === job.jobId) {
      setPendingDel('')
    } else {
      setPendingDel(job.jobId)
      return
    }
    try {
      await api.deleteJob(job.jobId)
      setList((prev) => prev.filter((x) => x.jobId !== job.jobId))
      onChanged?.()
    } catch (e) {
      setErr((e as ApiError)?.message || '删除失败')
      setPendingDel('')
    }
  }

  const quota = me?.quota
  const quotaPct = quota && quota.limit ? Math.min(100, Math.round((quota.used / quota.limit) * 100)) : 0

  return (
    <Modal
      open={open}
      title="我的生成记录"
      subtitle="按账号保存在服务端，换设备登录也能看到；产物文件保留 24 小时"
      icon="history"
      width={520}
      onClose={() => {
        setPendingDel('')
        onClose()
      }}
      footer={
        <div className="row" style={{ width: '100%', alignItems: 'center' }}>
          {quota && (
            <div className="quota-meter" style={{ flex: 1 }}>
              <span className="t-sm muted">今日 {quota.used} / {quota.limit}</span>
              <span className={`quota-bar${quota.remaining === 0 ? ' full' : ''}`}>
                <i style={{ width: `${quotaPct}%` }} />
              </span>
            </div>
          )}
          <button className="btn btn-ghost btn-sm" onClick={() => void load()} disabled={state === 'loading'}>
            <Icon name="refresh" size={13} />
            刷新
          </button>
        </div>
      }
    >
      {err && (
        <div className="notice" style={{ marginBottom: 12 }}>
          <Icon name="alert" size={13} />
          {err}
        </div>
      )}

      {state === 'loading' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} h={62} />
          ))}
        </div>
      )}

      {state === 'ready' && list.length === 0 && (
        <Empty icon="history" title="还没有生成记录" desc="运行一次生成任务后，这里会按账号累积你的历史记录" />
      )}

      {state === 'ready' &&
        list.map((h) => {
          const st = STATUS[h.status] ?? STATUS.queued
          const live = h.status === 'running' || h.status === 'queued'
          const openable = live || h.hasResult
          return (
            <div key={h.jobId} className={`history-item${openable ? '' : ' off'}`} style={{ alignItems: 'flex-start' }}>
              <button
                type="button"
                className="hist-item"
                style={{ flex: 1, minWidth: 0, background: 'none', border: 0, font: 'inherit', cursor: openable ? 'pointer' : 'default' }}
                onClick={() => openable && onPick(h)}
                disabled={!openable}
              >
                <span className="hi-ic">
                  <Icon name={h.status === 'error' ? 'alert' : h.status === 'cancelled' ? 'x' : 'package'} size={15} />
                </span>
                <span className="hi-body">
                  <span className="hi-t truncate">{h.productName || '未命名商品'}</span>
                  <span className="hi-d">
                    {relTime(h.createdAt)} · {new Date(h.createdAt * 1000).toLocaleString('zh-CN')}
                  </span>
                  <span className="hi-tags">
                    <span className={`badge ${st.cls}`}>{st.label}</span>
                    {(h.platformNames ?? []).slice(0, 3).map((n) => (
                      <span className="hi-tag" key={n}>{n}</span>
                    ))}
                    {(h.platformNames?.length ?? 0) > 3 && (
                      <span className="hi-tag">+{(h.platformNames?.length ?? 0) - 3}</span>
                    )}
                    {h.jobCount ? <span className="hi-tag">{h.jobCount} 站点</span> : null}
                    {!openable && <span className="hi-tag">产物已过期</span>}
                  </span>
                  {h.error && <span className="hi-d clamp-2" style={{ color: 'var(--err-600)' }}>{h.error}</span>}
                </span>
                {openable && <Icon name="chevronRight" size={15} />}
              </button>
              <span className="hi-act">
                {pendingDel === h.jobId ? (
                  <button
                    className="btn btn-danger btn-sm"
                    onClick={() => void remove(h)}
                    title="确认删除这条记录与其产物"
                  >
                    确认删除
                  </button>
                ) : (
                  <button className="hi-x" onClick={() => void remove(h)} title="删除这条记录" aria-label="删除记录">
                    <Icon name="x" size={13} />
                  </button>
                )}
              </span>
            </div>
          )
        })}
    </Modal>
  )
}
