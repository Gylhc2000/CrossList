'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ConfigModal from '@/components/ConfigModal'
import DownloadView from '@/components/DownloadView'
import HistoryModal from '@/components/HistoryModal'
import InputView, { type StartPayload } from '@/components/InputView'
import PreviewView from '@/components/PreviewView'
import ProgressView from '@/components/ProgressView'
import { Icon, ToastStack, type IconName, type ToastItem } from '@/components/ui'
import { api, streamUrl } from '@/lib/api'
import type { AppConfig, JobSnapshot, MetaResponse, PlatformFiles, SseEvent } from '@/lib/types'

type ViewName = 'input' | 'progress' | 'preview' | 'download'

// 【功能开关】自定义模型服务（自定义 Base URL / API Key / 模型名，弹窗为 ConfigModal.tsx，
// 后端接口在 backend/app/api/config_api.py）。演示版暂隐藏入口：改为 true 后
// header 右上角的齿轮按钮即恢复显示。
const ENABLE_MODEL_CONFIG = false

const emptyJob = (): JobSnapshot => ({
  id: '',
  status: 'queued',
  steps: [],
  logs: [],
  warnings: [],
  result: {},
  error: null,
  started_at: null,
  finished_at: null,
})

const STEPS: { key: ViewName; label: string; icon: IconName }[] = [
  { key: 'input', label: '新建任务', icon: 'package' },
  { key: 'progress', label: '处理进度', icon: 'cpu' },
  { key: 'preview', label: '生成结果', icon: 'eye' },
  { key: 'download', label: '素材包下载', icon: 'download' },
]

function applyEvent(prev: JobSnapshot, e: SseEvent): JobSnapshot {
  switch (e.type) {
    case 'hello':
      return e.job
    case 'step': {
      const steps = [...prev.steps]
      steps[e.index] = {
        ...steps[e.index],
        state: e.state as JobSnapshot['steps'][0]['state'],
        detail: e.detail,
      }
      return { ...prev, steps }
    }
    case 'log':
      return {
        ...prev,
        logs: [...prev.logs, { ts: Date.now(), msg: e.msg, level: e.level as 'info' }],
      }
    case 'card':
      return { ...prev, result: { ...prev.result, knowledge_card: e.card } }
    case 'listing':
      return {
        ...prev,
        result: {
          ...prev.result,
          listings: { ...(prev.result.listings ?? {}), [e.market.lang_code]: e.listing },
        },
      }
    case 'listings':
      return { ...prev, result: { ...prev.result, listings: e.listings } }
    case 'image':
      return { ...prev, result: { ...prev.result, images: [...(prev.result.images ?? []), e.image] } }
    case 'platforms':
      return { ...prev, result: { ...prev.result, platforms: e.platforms } }
    case 'done':
      return {
        ...prev,
        status: 'done',
        started_at: e.started_at ?? prev.started_at,
        finished_at: e.finished_at ?? prev.finished_at,
        result: { ...prev.result, ...e.result },
      }
    case 'fail':
      return { ...prev, status: 'error', error: e.error }
    default:
      return prev
  }
}

export default function Home() {
  const [view, setView] = useState<ViewName>('input')
  const [meta, setMeta] = useState<MetaResponse | null>(null)
  const [config, setConfig] = useState<AppConfig | null>(null)
  const [job, setJob] = useState<JobSnapshot | null>(null)
  const [files, setFiles] = useState<PlatformFiles[]>([])
  const [filesState, setFilesState] = useState<'idle' | 'loading' | 'ready'>('idle')
  const [activeTab, setActiveTab] = useState('')
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const [starting, setStarting] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [configOpen, setConfigOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)

  const esRef = useRef<EventSource | null>(null)
  const startRef = useRef(0)
  const toastSeq = useRef(0)

  const pushToast = useCallback((msg: string, kind: ToastItem['kind'] = 'info') => {
    const id = ++toastSeq.current
    setToasts((prev) => [...prev.slice(-2), { id, msg, kind }])
    window.setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 3600)
  }, [])

  // 初始化
  useEffect(() => {
    api.meta().then(setMeta).catch(() => setMeta(null))
    api.getConfig().then(setConfig).catch(() => setConfig(null))
  }, [])

  // 计时
  useEffect(() => {
    if (job?.status !== 'running') return
    const t = window.setInterval(() => {
      setElapsed(Math.round((Date.now() - startRef.current) / 1000))
    }, 1000)
    return () => window.clearInterval(t)
  }, [job?.status])

  useEffect(() => () => esRef.current?.close(), [])

  const fetchFiles = useCallback(async (jobId: string) => {
    setFilesState('loading')
    try {
      const d = await api.files(jobId)
      setFiles(d.platforms ?? [])
      setFilesState('ready')
    } catch {
      setFiles([])
      setFilesState('ready')
    }
  }, [])

  const openStream = useCallback(
    (jobId: string) => {
      esRef.current?.close()
      const es = new EventSource(streamUrl(jobId))
      esRef.current = es
      es.onmessage = (ev) => {
        let e: SseEvent
        try {
          e = JSON.parse(ev.data)
        } catch {
          return
        }
        setJob((prev) => applyEvent(prev ?? emptyJob(), e))
        if (e.type === 'done') {
          const first = e.result.platforms?.[0]?.key
          if (first) setActiveTab(first)
          void fetchFiles(jobId)
          setView('preview')
          pushToast('素材包生成完成，已自动跳转结果预览', 'ok')
          es.close()
        } else if (e.type === 'fail') {
          pushToast('任务失败：' + e.error, 'err')
          es.close()
        } else if (e.type === 'end') {
          es.close()
        }
      }
      es.onerror = () => {
        /* 浏览器会自动重连 */
      }
    },
    [fetchFiles, pushToast],
  )

  const handleStart = async (payload: StartPayload) => {
    setStarting(true)
    try {
      const { jobId } = await api.createJob(payload)
      setJob({ ...emptyJob(), id: jobId, status: 'running' })
      setFiles([])
      setFilesState('idle')
      setActiveTab('')
      startRef.current = Date.now()
      setElapsed(0)
      setView('progress')
      openStream(jobId)
      try {
        const { saveHistory } = await import('@/components/HistoryModal')
        saveHistory(jobId, payload.productName)
      } catch {
        /* ignore */
      }
    } catch (e) {
      pushToast('启动失败：' + (e as Error).message, 'err')
    } finally {
      setStarting(false)
    }
  }

  const handlePickHistory = async (jobId: string) => {
    try {
      const snap = await api.snapshot(jobId)
      setJob(snap)
      setActiveTab(snap.result.platforms?.[0]?.key ?? '')
      void fetchFiles(jobId)
      setView('preview')
      pushToast('已载入历史任务结果', 'ok')
    } catch {
      pushToast('该任务已不在服务内存中（后端可能已重启）', 'err')
    }
  }

  const hasResult = (job?.result.platforms ?? []).length > 0

  const goto = (v: ViewName) => {
    if (v !== 'input' && !job) {
      pushToast('请先创建并运行一个任务', 'err')
      return
    }
    if (v === 'preview' && !hasResult) {
      pushToast('结果尚未生成，请先运行任务', 'err')
      return
    }
    setView(v)
  }

  const stepDone = useMemo<Record<ViewName, boolean>>(
    () => ({
      input: !!job,
      progress: job?.status === 'done' || job?.status === 'error' || job?.status === 'cancelled',
      preview: hasResult,
      download: hasResult && files.length > 0,
    }),
    [job, hasResult, files.length],
  )

  const stepEnabled = (k: ViewName) => (k === 'input' ? true : !!job && (k !== 'preview' || hasResult))

  return (
    <div className="app-shell">
      <header className="app-header">
        <div className="header-inner">
          <div className="brand">
            <div className="brand-mark">
              <Icon name="sparkles" size={17} strokeWidth={2} />
            </div>
            <div className="brand-text">
              <span className="brand-name">跨境智上 Agent</span>
              <span className="brand-sub">CrossList AI</span>
            </div>
          </div>

          <nav className="stepper" aria-label="流程导航">
            {STEPS.map((s, i) => (
              <div key={s.key} style={{ display: 'flex', alignItems: 'center' }}>
                {i > 0 && <span className="stp-sep" />}
                <button
                  className={`stp${view === s.key ? ' active' : ''}${stepDone[s.key] ? ' done' : ''}`}
                  onClick={() => goto(s.key)}
                  disabled={!stepEnabled(s.key)}
                  aria-current={view === s.key ? 'step' : undefined}
                >
                  <span className="stp-idx">
                    {stepDone[s.key] ? <Icon name="check" size={11} strokeWidth={3} /> : i + 1}
                  </span>
                  <span className="stp-label">{s.label}</span>
                </button>
              </div>
            ))}
          </nav>

          <div className="header-actions">
            <span
              className={`key-pill ${config?.hasKey ? 'ok' : 'miss'}`}
              title={config?.hasKey ? 'API Key 已配置' : '尚未配置 API Key'}
            >
              <span className="key-dot" />
              {config?.hasKey ? '已接入模型' : '未配置 Key'}
            </span>
            <button className="icon-btn" onClick={() => setHistoryOpen(true)} title="历史记录">
              <Icon name="history" size={16} />
            </button>
            {ENABLE_MODEL_CONFIG && (
              <button className="icon-btn" onClick={() => setConfigOpen(true)} title="模型配置">
                <Icon name="settings" size={16} />
              </button>
            )}
          </div>
        </div>
      </header>

      <main className="app-main">
        <div className="view-enter" key={view}>
          {view === 'input' && (
            <InputView
              meta={meta}
              starting={starting}
              onStart={handleStart}
              onOpenConfig={() => setConfigOpen(true)}
            />
          )}
          {view === 'progress' && (
            <ProgressView
              job={job}
              elapsed={elapsed}
              onCancel={async () => {
                if (!job?.id) return
                await api.cancel(job.id)
                pushToast('已请求取消，正在停止…')
              }}
              onViewResult={() => setView('preview')}
            />
          )}
          {view === 'preview' && job && (
            <PreviewView
              job={job}
              activeTab={activeTab}
              onTabChange={setActiveTab}
              onGoDownload={() => setView('download')}
              onBackProgress={() => setView('progress')}
            />
          )}
          {view === 'download' && job && (
            <DownloadView
              job={job}
              files={files}
              filesState={filesState}
              onReload={() => job?.id && void fetchFiles(job.id)}
              onBackPreview={() => setView('preview')}
              onRestart={() => setView('input')}
              onToast={pushToast}
            />
          )}
        </div>
      </main>

      <ConfigModal
        open={configOpen}
        meta={meta}
        config={config}
        onClose={() => setConfigOpen(false)}
        onToast={pushToast}
        onSaved={(patch) => setConfig((c) => ({ ...c, ...patch }) as AppConfig)}
      />
      <HistoryModal open={historyOpen} onClose={() => setHistoryOpen(false)} onPick={handlePickHistory} />

      <ToastStack items={toasts} />
    </div>
  )
}
