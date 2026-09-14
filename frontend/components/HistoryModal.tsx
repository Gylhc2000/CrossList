'use client'

import { useEffect, useState } from 'react'
import { Empty, Icon, Modal } from '@/components/ui'

export interface HistoryEntry {
  jobId: string
  name: string
  ts: number
}

const KEY = 'crosslist_history'

export function loadHistory(): HistoryEntry[] {
  if (typeof window === 'undefined') return []
  try {
    return JSON.parse(localStorage.getItem(KEY) || '[]') as HistoryEntry[]
  } catch {
    return []
  }
}

export function saveHistory(jobId: string, name: string) {
  if (typeof window === 'undefined') return
  try {
    const list = loadHistory()
    list.unshift({ jobId, name, ts: Date.now() })
    localStorage.setItem(KEY, JSON.stringify(list.slice(0, 20)))
  } catch {
    /* ignore */
  }
}

function relTime(ts: number) {
  const d = Math.floor((Date.now() - ts) / 1000)
  if (d < 60) return '刚刚'
  if (d < 3600) return `${Math.floor(d / 60)} 分钟前`
  if (d < 86400) return `${Math.floor(d / 3600)} 小时前`
  return `${Math.floor(d / 86400)} 天前`
}

interface Props {
  open: boolean
  onClose: () => void
  onPick: (jobId: string) => void
}

export default function HistoryModal({ open, onClose, onPick }: Props) {
  const [list, setList] = useState<HistoryEntry[]>([])

  useEffect(() => {
    if (open) setList(loadHistory())
  }, [open])

  const clear = () => {
    try {
      localStorage.removeItem(KEY)
      setList([])
    } catch {
      /* ignore */
    }
  }

  return (
    <Modal
      open={open}
      title="历史任务"
      subtitle="仅保存在本机浏览器，后端重启后任务可能失效"
      icon="history"
      width={460}
      onClose={onClose}
      footer={
        list.length > 0 ? (
          <button className="btn btn-danger btn-sm" onClick={clear}>
            <Icon name="x" size={13} />
            清空记录
          </button>
        ) : undefined
      }
    >
      {list.length === 0 ? (
        <Empty icon="history" title="暂无历史任务" desc="运行一次生成任务后，这里会保留最近 20 条记录" />
      ) : (
        list.map((h) => (
          <button
            key={h.jobId}
            className="history-item"
            onClick={() => {
              onPick(h.jobId)
              onClose()
            }}
          >
            <span className="hi-ic">
              <Icon name="package" size={15} />
            </span>
            <span style={{ flex: 1, minWidth: 0 }}>
              <span className="hi-t">{h.name || '未命名商品'}</span>
              <span className="hi-d">
                {relTime(h.ts)} · {new Date(h.ts).toLocaleString('zh-CN')}
              </span>
              <span className="hi-d mono" style={{ opacity: 0.7 }}>
                {h.jobId}
              </span>
            </span>
            <Icon name="chevronRight" size={15} />
          </button>
        ))
      )}
    </Modal>
  )
}
