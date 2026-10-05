'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import AuthView from '@/components/AuthView'
import ConfigModal from '@/components/ConfigModal'
import DownloadView from '@/components/DownloadView'
import HistoryModal from '@/components/HistoryModal'
import InputView, { type StartPayload } from '@/components/InputView'
import PasswordModal from '@/components/PasswordModal'
import PreviewView from '@/components/PreviewView'
import ProgressView from '@/components/ProgressView'
import { Icon, ToastStack, type IconName, type ToastItem } from '@/components/ui'
import { ApiError, api, setUnauthorizedHandler, streamUrl } from '@/lib/api'
import type {
  AppConfig,
  HistoryJob,
  JobSnapshot,
  MetaResponse,
  PlatformFiles,
  SseEvent,
  UserMe,
} from '@/lib/types'

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
    case 'start': {
      // 后端拿到并发槽位、真正开跑时才会发这个事件：它是「排队 → 运行」的唯一信号。
      // 漏掉它会让排队任务的计时器不启动、取消按钮一直禁用。
      const steps = e.steps?.length ? e.steps : prev.steps
      return {
        ...prev,
        steps,
        status: prev.status === 'queued' ? 'running' : prev.status,
      }
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
          // 键必须与后端 listings 的单元键一致（"平台:市场"）。
          // 用 lang_code 会让同语言的不同平台互相覆盖（如 amazon:us 与 shopee:us 都是 en）
          listings: { ...(prev.result.listings ?? {}), [e.market.key]: e.listing },
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
  const [me, setMe] = useState<UserMe | null>(null)
  // 会话在 HttpOnly Cookie 里，前端读不到，只能问后端"我是谁"来判断是否已登录
  const [auth, setAuth] = useState<'checking' | 'anon' | 'ready'>('checking')
  const [job, setJob] = useState<JobSnapshot | null>(null)
  const [files, setFiles] = useState<PlatformFiles[]>([])
  // 'error' 与 'ready 且空列表' 是两回事：前者是这次请求没成功，后者才可能是产物真没了。
  // 混在一起会让一次网络抖动显示成"产物已失效"，骗用户重跑一次任务
  const [filesState, setFilesState] = useState<'idle' | 'loading' | 'ready' | 'error'>('idle')
  const [activeTab, setActiveTab] = useState('')
  const [toasts, setToasts] = useState<ToastItem[]>([])
  const [starting, setStarting] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [configOpen, setConfigOpen] = useState(false)
  const [historyOpen, setHistoryOpen] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const [pwOpen, setPwOpen] = useState(false)

  const esRef = useRef<EventSource | null>(null)
  const stopRef = useRef<(() => void) | null>(null)
  const startRef = useRef(0)
  const toastSeq = useRef(0)

  const pushToast = useCallback((msg: string, kind: ToastItem['kind'] = 'info') => {
    const id = ++toastSeq.current
    setToasts((prev) => [...prev.slice(-2), { id, msg, kind }])
    window.setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 3600)
  }, [])

  const dropAuth = useCallback(() => {
    stopRef.current?.()
    esRef.current?.close()
    esRef.current = null
    setMe(null)
    setAuth('anon')
    setMenuOpen(false)
    setJob(null)
    setFiles([])
    setView('input')
    setPwOpen(false)
  }, [])

  const loadWorkspace = useCallback(async () => {
    api.meta().then(setMeta).catch(() => setMeta(null))
    // 模型配置入口默认关闭：关着还去拉 /api/config，只会多一次带掩码 Key 的无谓往返
    if (ENABLE_MODEL_CONFIG) {
      api.getConfig().then(setConfig).catch(() => setConfig(null))
    }
  }, [])

  /** 只刷额度：创建任务/删除记录后调用，不动其它状态 */
  const refreshMe = useCallback(async () => {
    try {
      setMe(await api.me())
    } catch {
      // 会话过期/被撤销：退回登录页，别留一个报错的空工作台
      setAuth('anon')
    }
  }, [])

  // 会话过期时后端返回 401。进度页的看门狗、产物拉取、取消请求都会撞上它，
  // 逐个判断太容易漏 —— 在 api 层统一上报，这里一处收口退回登录页。
  useEffect(() => {
    setUnauthorizedHandler(dropAuth)
    return () => setUnauthorizedHandler(null)
  }, [dropAuth])

  // 启动：先确认身份（会话在 HttpOnly Cookie 里，只能问后端"我是谁"），
  // 通过后才能拿只有登录态可见的元信息
  useEffect(() => {
    let alive = true
    api
      .me()
      .then((u) => {
        if (!alive) return
        setMe(u)
        setAuth('ready')
        void loadWorkspace()
      })
      .catch(() => alive && setAuth('anon'))
    return () => {
      alive = false
    }
  }, [loadWorkspace])

  // 计时
  useEffect(() => {
    if (job?.status !== 'running') return
    const t = window.setInterval(() => {
      setElapsed(Math.round((Date.now() - startRef.current) / 1000))
    }, 1000)
    return () => window.clearInterval(t)
  }, [job?.status])

  // 卸载时既要关流也要停看门狗定时器：只关 ES 会让 15s 的 interval 一直跑，
  // 并在已卸载的组件上继续 setJob
  useEffect(
    () => () => {
      stopRef.current?.()
      esRef.current?.close()
      esRef.current = null
    },
    [],
  )

  const fetchFiles = useCallback(
    async (jobId: string) => {
      setFilesState('loading')
      try {
        const d = await api.files(jobId)
        setFiles(d.platforms ?? [])
        setFilesState('ready')
      } catch {
        setFiles([])
        setFilesState('error')
      }
    },
    [],
  )

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
      // 上一条流可能还带着自己的看门狗，先整体停掉再开新的
      stopRef.current?.()
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
        if (stopRef.current === stop) stopRef.current = null
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
          // 请求期间流已被停掉（卸载或换了任务）：不要再回写状态
          if (stopped) return
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
      stopRef.current = stop

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
      void refreshMe()      // 额度是按人算的，创建完就把剩余次数刷回来
    } catch (e) {
      const err = e as ApiError
      if (err?.status === 401) {
        dropAuth()
        return
      }
      // 后端把 429（额度/排队）与 400（参数不合法）写成了人话，直接转达
      pushToast((err?.message && err.message) || '启动失败', 'err')
    } finally {
      setStarting(false)
    }
  }

  /** 按 jobId 打开任务：还在跑就接管事件流，已结束就载入快照并落到结果页。
   *  历史面板与地址栏 ?job= 共用这一条路径，避免两边行为漂移。 */
  const openJob = useCallback(
    async (jobId: string) => {
      try {
        const snap = await api.snapshot(jobId)
        stopRef.current?.()
        esRef.current?.close()
        esRef.current = null
        if (snap.status === 'running' || snap.status === 'queued') {
          startRef.current = Date.now()
          setElapsed(0)
          setJob({ ...emptyJob(), id: jobId, status: snap.status })
          setView('progress')
          openStream(jobId)
          return
        }
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
        // 与 resultReady 同一把尺子：失败/取消的记录即便带着半成品 Listing，
        // 也绝不跳进结果页（goto 的门槛拦得住点击，拦不住这条直设视图的路径）
        setView(snap.status === 'done' && snap.result.platforms?.length ? 'preview' : 'progress')
        if (snap.cold) pushToast('已载入历史记录（结果由服务端存档重建）', 'ok')
      } catch (e) {
        const err = e as ApiError
        if (err?.status === 401) dropAuth()
        else pushToast(err?.message || '无法载入该条历史记录', 'err')
      }
    },
    [openStream, fetchFiles, pushToast, dropAuth],
  )

  const handlePickHistory = (h: HistoryJob) => {
    setHistoryOpen(false)
    void openJob(h.jobId)
  }

  // 刷新后回到同一个任务：后端记录本来就跨重启存活，没必要把结果丢回输入页。
  // 必须在**首次渲染时**就把 ?job= 抄进 ref：身份确认前不会走到恢复分支，
  // 而下面同步地址栏的 effect 是无条件跑的，晚一步读就被它先把参数抹掉了。
  const pendingJobRef = useRef<string | null>(
    typeof window === 'undefined' ? null : new URLSearchParams(window.location.search).get('job'),
  )
  useEffect(() => {
    if (auth !== 'ready' || !pendingJobRef.current) return
    const id = pendingJobRef.current
    pendingJobRef.current = null
    void openJob(id)
  }, [auth, openJob])

  // replaceState 而不是 pushState：不新增历史条目，浏览器后退仍然是"离开这个页面"
  useEffect(() => {
    const q = new URLSearchParams(window.location.search)
    if (job?.id && view !== 'input') q.set('job', job.id)
    else q.delete('job')
    const s = q.toString()
    window.history.replaceState(null, '', s ? `?${s}` : window.location.pathname)
  }, [job?.id, view])

  const logout = async () => {
    setMenuOpen(false)
    try {
      await api.logout()
    } catch {
      /* 服务端会话本来就没有也无妨，本地一律退回登录页 */
    }
    dropAuth()
  }

  const hasResult = (job?.result.platforms ?? []).length > 0
  // 只有真正 done 才算"有交付物"：运行中的 result 是边生成边补的半成品（platforms 在
  // validate 阶段就已推送，早于 fix/export）；而**失败/取消**的任务同样可能带着几条
  // 半成品 Listing，却永远不会有模板和对照表 —— 放它进结果页只会看到一堆空产物。
  const resultReady = hasResult && job?.status === 'done'

  // 失败/取消的任务没有可看的交付物，进度页之外都不该开放
  const jobFailed = job?.status === 'error' || job?.status === 'cancelled'

  const goto = (v: ViewName) => {
    if (v !== 'input' && !job) {
      pushToast('请先创建并运行一个任务', 'err')
      return
    }
    if ((v === 'preview' || v === 'download') && !resultReady) {
      pushToast(
        job?.status === 'running'
          ? '任务还在执行中，可在进度页的「实时产出」查看已完成内容'
          : jobFailed
            ? '任务未成功结束，没有可交付的结果'
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
      // 只有真正跑完才算"完成"：失败/取消曾在里是绿色对勾
      progress: job?.status === 'done',
      preview: resultReady,
      download: resultReady && files.length > 0,
    }),
    [job, resultReady, files.length],
  )

  // 与 goto 的门槛保持一致，否则会出现"按钮可点、点了被拒"
  const stepEnabled = (k: ViewName) =>
    k === 'input' ? true : k === 'preview' || k === 'download' ? resultReady : !!job

  if (auth !== 'ready') {
    if (auth === 'anon') {
      return (
        <AuthView
          toast={pushToast}
          onAuthed={(u) => {
            setMe(u)
            setAuth('ready')
            void loadWorkspace()
          }}
        />
      )
    }
    return (
      <div className="app-shell">
        <main className="app-main auth-main">
          <div className="auth-card" style={{ textAlign: 'center' }}>
            <div className="auth-title">跨境智上 Agent</div>
            <div className="auth-sub">正在确认登录状态…</div>
          </div>
        </main>
      </div>
    )
  }

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
            {STEPS.map((s, i) => {
              // 失败/取消标在"进度"这一步：它是终态发生的地方，
              // 标在输入步会让人以为商品信息填错了
              const bad = jobFailed && s.key === 'progress'
              return (
              <div key={s.key} style={{ display: 'flex', alignItems: 'center' }}>
                {i > 0 && <span className="stp-sep" />}
                <button
                  className={`stp${view === s.key ? ' active' : ''}${stepDone[s.key] ? ' done' : ''}${bad ? ' bad' : ''}`}
                  onClick={() => goto(s.key)}
                  disabled={!stepEnabled(s.key)}
                  aria-current={view === s.key ? 'step' : undefined}
                  title={bad ? (job?.status === 'cancelled' ? '任务已取消' : job?.error || '任务失败') : undefined}
                >
                  <span className="stp-idx">
                    {bad ? (
                      <Icon name="x" size={11} strokeWidth={3} />
                    ) : stepDone[s.key] ? (
                      <Icon name="check" size={11} strokeWidth={3} />
                    ) : (
                      i + 1
                    )}
                  </span>
                  <span className="stp-label">{s.label}</span>
                </button>
              </div>
              )
            })}
          </nav>

          <div className="header-actions">
            {ENABLE_MODEL_CONFIG && (
              <span
                className={`key-pill ${config?.hasKey ? 'ok' : 'miss'}`}
                title={config?.hasKey ? 'API Key 已配置' : '尚未配置 API Key'}
              >
                <span className="key-dot" />
                {config?.hasKey ? '已接入模型' : '未配置 Key'}
              </span>
            )}
            <button className="icon-btn" onClick={() => setHistoryOpen(true)} title="我的生成记录" aria-label="我的生成记录">
              <Icon name="history" size={16} />
            </button>
            {ENABLE_MODEL_CONFIG && (
              <button className="icon-btn" onClick={() => setConfigOpen(true)} title="模型配置" aria-label="模型配置">
                <Icon name="settings" size={16} />
              </button>
            )}
            <button
              className="user-chip"
              onClick={() => setMenuOpen((v) => !v)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              title="账号与今日额度"
            >
              <span className="user-avatar">{(me?.username ?? '?').slice(0, 1).toUpperCase()}</span>
              <span className="user-name">{me?.username}</span>
              <Icon name="chevronDown" size={12} />
            </button>

            {menuOpen && (
              <>
                <div
                  onClick={() => setMenuOpen(false)}
                  style={{ position: 'fixed', inset: 0, zIndex: 39 }}
                  aria-hidden
                />
                <div className="user-menu" role="menu">
                  <div className="um-head">
                    <div className="um-name">
                      {me?.username}
                      {me?.isAdmin && <span className="badge badge-ai" style={{ marginLeft: 6 }}>管理员</span>}
                    </div>
                    {me?.quota && (
                      <div className="um-meta">
                        <span>今日 {me.quota.used} / {me.quota.limit}</span>
                        <span className={`quota-bar${me.quota.remaining === 0 ? ' full' : ''}`} style={{ flex: 'none', width: 54 }}>
                          <i style={{ width: `${Math.min(100, Math.round((me.quota.used / Math.max(1, me.quota.limit)) * 100))}%` }} />
                        </span>
                      </div>
                    )}
                  </div>
                  <button
                    className="um-item"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false)
                      setHistoryOpen(true)
                    }}
                  >
                    <Icon name="history" size={14} />
                    我的生成记录
                  </button>
                  <button
                    className="um-item"
                    role="menuitem"
                    onClick={() => {
                      setMenuOpen(false)
                      setPwOpen(true)
                    }}
                  >
                    <Icon name="shield" size={14} />
                    修改口令
                  </button>
                  <div className="um-sep" />
                  <button className="um-item danger" role="menuitem" onClick={() => void logout()}>
                    <Icon name="x" size={14} />
                    退出登录
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      </header>

      <main className="app-main">
        <div className="view-enter" key={view}>
          {job?.cold && view !== 'input' && (
            <div className="cold-note">
              <Icon name="clock" size={13} />
              这是一条历史记录的存档结果：实时日志与进度已不再更新，产物若超过保留期需重新生成。
            </div>
          )}
          {view === 'input' && (
            <InputView meta={meta} starting={starting} onStart={handleStart} />
          )}
          {view === 'progress' && (
            <ProgressView
              job={job}
              elapsed={elapsed}
              onCancel={async () => {
                if (!job?.id) return
                try {
                  const r = await api.cancel(job.id)
                  // 任务可能刚好在这一瞬自己结束，cancel 返回 false 不是失败
                  pushToast(r.ok ? '已请求取消，正在停止…' : '任务已结束，无需取消')
                } catch (e) {
                  const err = e as ApiError
                  if (err?.status === 401) dropAuth()
                  else pushToast(err?.message || '取消请求未送达，请稍后重试', 'err')
                }
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
      <HistoryModal
        open={historyOpen}
        onClose={() => setHistoryOpen(false)}
        onPick={handlePickHistory}
        me={me}
        onChanged={() => void refreshMe()}
      />

      <PasswordModal
        open={pwOpen}
        username={me?.username ?? ''}
        onClose={() => setPwOpen(false)}
        onDone={() => void refreshMe()}
        onToast={pushToast}
      />

      <ToastStack items={toasts} />
    </div>
  )
}
