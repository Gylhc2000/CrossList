'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import ConfigModal from '@/components/ConfigModal'
import DownloadView from '@/components/DownloadView'
import HistoryModal from '@/components/HistoryModal'
import InputView, { type StartPayload } from '@/components/InputView'
import PreviewView from '@/components/PreviewView'
import ProgressView from '@/components/ProgressView'
import { Icon, ToastStack, type IconName, type ToastItem } from '@/components/ui'
import { ApiError, api, streamUrl } from '@/lib/api'
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
    case 'hello': {
      // 重连时服务端会重发权威快照。注意：运行中的快照 result 是空的
      // （结果只在终态才写进 JobManager），整体替换会把 SSE 已累积的
      // listings/images 冲掉 —— 所以快照没带 result 时保留本地已收到的部分。
      const j = e.job
      return {
        ...j,
        result: Object.keys(j.result ?? {}).length ? j.result : prev.result,
      }
    }
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

  // 任务终态收尾：切到第一个站点、拉产物、跳结果页
  const onJobDone = useCallback(
    (result: JobSnapshot['result'], jobId: string) => {
      const first = result.platforms?.[0]?.key
      if (first) setActiveTab(first)
      void fetchFiles(jobId)
      setView('preview')
      pushToast('素材包生成完成，已自动跳转结果预览', 'ok')
    },
    [fetchFiles, pushToast],
  )

  const openStream = useCallback(
    (jobId: string) => {
      esRef.current?.close()
      const es = new EventSource(streamUrl(jobId))
      esRef.current = es

      let stopped = false
      let probing = false
      let finalized = false
      let failures = 0
      let watchdog = 0
      const stop = () => {
        stopped = true
        if (watchdog) window.clearInterval(watchdog)
        es.close()
      }
      const finalize = (result: JobSnapshot['result'], a?: number | null, b?: number | null) => {
        if (finalized) return
        finalized = true
        // 用服务端时间戳定格用时，避免前端计时器把"等待重连"的时间也算进去
        if (a != null && b != null) setElapsed(Math.max(0, Math.round(b - a)))
        onJobDone(result ?? {}, jobId)
      }
      const loseTask = (msg: string) => {
        setJob((prev) => (prev ? { ...prev, status: 'error', error: msg } : prev))
        pushToast(msg, 'err')
        stop()
      }

      // 事件流断了必须能收敛。后端重启后 JobManager（内存态）里已没有这个任务，
      // 前端只会拿到 404 —— 若不管，UI 会永远停在 running：计时器一路涨到
      // "已运行 70 分钟"、进度节点永远不打勾。
      // 注意**不能只靠 es.onerror**：实测杀掉后端后浏览器只重连了一次便不再重试
      // （连接被拒会退避/放弃），靠 error 驱动的对账最多跑两次就断了。
      // 所以这里由看门狗定时主动对账，直到任务进入终态。
      const probe = async () => {
        // 已被新任务顶替的旧事件流不得再回写状态
        if (probing || stopped || esRef.current !== es) return
        probing = true
        try {
          const snap = await api.snapshot(jobId)
          failures = 0
          // 仍在进行中：不回写快照。运行期的快照 result 是空的（结果只在终态才落到 JobManager），
          // 直接替换会把 SSE 已经累积出来的 listings/images 冲掉。
          if (snap.status === 'running' || snap.status === 'queued') return
          setJob(snap)
          if (snap.status === 'done') {
            finalize(snap.result, snap.started_at, snap.finished_at)
          }
          stop()
        } catch (e) {
          if (e instanceof ApiError && e.status === 404) {
            // 后端活着但这个任务已不在内存里（重启过）—— 明确收敛
            loseTask('任务状态已丢失（后端已重启），请重新运行')
          } else {
            // 瞬时断网 / 后端正在重启：连续多次失败才判定失联
            failures += 1
            if (failures >= 5) loseTask('与后端连接中断，任务状态无法确认，请刷新后重新运行')
          }
        } finally {
          probing = false
        }
      }
      watchdog = window.setInterval(() => void probe(), 15000)

      es.onmessage = (ev) => {
        if (esRef.current !== es) return   // 旧流残余事件忽略，避免覆盖新任务状态
        let e: SseEvent
        try {
          e = JSON.parse(ev.data)
        } catch {
          return
        }
        setJob((prev) => applyEvent(prev ?? emptyJob(), e))
        if (e.type === 'done') {
          finalize(e.result, e.started_at, e.finished_at)
          stop()
        } else if (e.type === 'fail') {
          pushToast('任务失败：' + e.error, 'err')
          stop()
        } else if (e.type === 'end') {
          stop()
        } else if (e.type === 'hello') {
          // 重连时服务端会把权威快照重发一遍：若任务其实已经结束，这里直接收敛
          const snap = e.job
          if (snap.status === 'done') {
            finalize(snap.result, snap.started_at, snap.finished_at)
            stop()
          } else if (snap.status === 'error' || snap.status === 'cancelled') {
            finalized = true
            stop()
          }
        }
      }
      es.onerror = () => {
        // 连接抖动时提前对账一次，不用等下一个看门狗周期
        window.setTimeout(() => void probe(), 2000)
      }
    },
    [onJobDone, pushToast],
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
      // 历史任务的用时按服务端时间戳还原，别沿用上一次运行残留的计时器
      startRef.current = Date.now()
      setElapsed(
        snap.started_at != null && snap.finished_at != null
          ? Math.max(0, Math.round(snap.finished_at - snap.started_at))
          : 0,
      )
      void fetchFiles(jobId)
      setView('preview')
      pushToast('已载入历史任务结果', 'ok')
    } catch {
      pushToast('该任务已不在服务内存中（后端可能已重启）', 'err')
    }
  }

  const hasResult = (job?.result.platforms ?? []).length > 0
  // 「运行中」的 result 只是边生成边补的半成品（platforms 在 validate 阶段就已经推送，
  // 早于 fix/export）。若此时就允许进结果页，就会出现
  //「处理进度还没走完、生成结果却已经能看」的错位。
  // 结果页只在任务真正结束后开放；运行中想看产出用进度页的「实时产出」。
  const resultReady = hasResult && job?.status !== 'running' && job?.status !== 'queued'

  const goto = (v: ViewName) => {
    if (v !== 'input' && !job) {
      pushToast('请先创建并运行一个任务', 'err')
      return
    }
    if (v === 'preview' && !resultReady) {
      pushToast(
        job?.status === 'running'
          ? '任务还在执行中，可在进度页的「实时产出」查看已完成内容'
          : '结果尚未生成，请先运行任务',
        'err',
      )
      return
    }
    setView(v)
  }

  const stepDone = useMemo<Record<ViewName, boolean>>(
    () => ({
      input: !!job,
      progress: job?.status === 'done' || job?.status === 'error' || job?.status === 'cancelled',
      preview: resultReady,
      download: resultReady && files.length > 0,
    }),
    [job, resultReady, files.length],
  )

  const stepEnabled = (k: ViewName) =>
    k === 'input' ? true : !!job && (k !== 'preview' || resultReady)

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
