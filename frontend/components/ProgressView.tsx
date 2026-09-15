'use client'

import { useMemo, useState } from 'react'
import { AutoScroll, Empty, Icon, Segmented } from '@/components/ui'
import type { JobSnapshot, KnowledgeCard, Listing } from '@/lib/types'

interface Props {
  job: JobSnapshot | null
  elapsed: number
  onCancel: () => void
  onViewResult: () => void
}

type PaneKey = 'card' | 'listing' | 'image'

function fmtDuration(sec?: number | null): string {
  if (sec == null || !isFinite(sec) || sec < 0) return '—'
  const s = Math.round(sec)
  const m = Math.floor(s / 60)
  const r = s % 60
  return m > 0 ? `${m} 分 ${r} 秒` : `${r} 秒`
}

const timeFmt = (ts: number) =>
  new Date(ts).toLocaleTimeString('zh-CN', { hour12: false })

export default function ProgressView({ job, elapsed, onCancel, onViewResult }: Props) {
  const [pane, setPane] = useState<PaneKey>('card')
  // 运行日志默认收起：过程细节对普通用户是噪音；点击标题栏即可展开（演示时可展示 Agent 过程透明度）
  const [logOpen, setLogOpen] = useState(false)
  const steps = job?.steps ?? []
  const r = job?.result

  const done = steps.filter((s) => s.state === 'done' || s.state === 'skipped').length
  const running = steps.find((s) => s.state === 'running')
  const hasError = steps.some((s) => s.state === 'error')
  const pct = Math.max(
    0,
    Math.min(100, Math.round((done / Math.max(steps.length, 1)) * 100) - (running ? 6 : 0)),
  )

  const durationSec =
    job?.started_at != null && job?.finished_at != null
      ? job.finished_at - job.started_at
      : job?.status === 'running'
        ? elapsed
        : null

  const listings = useMemo(
    () => Object.values(r?.listings ?? {}) as Listing[],
    [r?.listings],
  )
  const images = r?.images ?? []
  const card: KnowledgeCard | undefined = r?.knowledge_card
  const imageOk = images.filter((i) => i.ok).length

  const statusMeta = (() => {
    switch (job?.status) {
      case 'done':
        return { cls: 'badge-ok', icon: 'checkCircle' as const, text: '全部阶段已完成' }
      case 'error':
        return { cls: 'badge-err', icon: 'alert' as const, text: `任务失败：${job.error ?? ''}` }
      case 'cancelled':
        return { cls: 'badge-neutral', icon: 'x' as const, text: '任务已取消' }
      default:
        return { cls: 'badge-brand', icon: 'cpu' as const, text: 'Agent 正在执行…' }
    }
  })()

  return (
    <section>
      <div className="page-head">
        <div>
          <h1 className="page-title">
            Agent 处理进度
            <span className={`badge ${statusMeta.cls}`}>
              <Icon name={statusMeta.icon} size={12} />
              {statusMeta.text}
            </span>
          </h1>
          <p className="page-sub">
            {running ? `当前阶段：${running.title}` : '正在自动执行各步骤，可随时取消'}
          </p>
        </div>
        <div className="page-head-aside">
          {/* 拿不到可信用时就不显示（例如任务状态已丢失时，前端计时器会把等待重连的时间也算进去，
              显示成"已运行 70 分钟"是误导） */}
          {durationSec != null && (
            <span className="badge badge-neutral">
              <Icon name="clock" size={11} />
              {job?.status === 'done'
                ? `用时 ${fmtDuration(durationSec)}`
                : `已运行 ${fmtDuration(durationSec)}`}
            </span>
          )}
          <button
            className="btn btn-danger btn-sm"
            onClick={onCancel}
            disabled={job?.status !== 'running'}
          >
            <Icon name="x" size={13} />
            取消任务
          </button>
        </div>
      </div>

      {/* 总体进度 */}
      <div className="section-card" style={{ marginBottom: 18 }}>
        <div className="section-body" style={{ padding: '18px 22px' }}>
          <div className="progress-head">
            <span className="strong">
              {done} / {steps.length || '—'} 个阶段完成
            </span>
            <span className="muted num">{pct}%</span>
          </div>
          <div className="progress-bar-bg">
            <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
          </div>
        </div>
      </div>

      <div className="split halves">
        {/* ---------- 左：时间线 + 实时产出 ---------- */}
        <div className="stack">
          <div className="section-card">
            <div className="section-head">
              <span className="section-idx">
                <Icon name="cpu" size={13} />
              </span>
              <span className="section-title">执行链路</span>
              <span className="section-desc">AI 自动规划 · 逐步执行</span>
            </div>
            <div className="section-body">
              {steps.length === 0 ? (
                <Empty icon="cpu" title="等待任务启动…" desc="正在连接 Agent 事件流" />
              ) : (
                <ul className="timeline">
                  {steps.map((s, i) => (
                    <li className={`tl-item ${s.state}`} key={s.key}>
                      <div className="tl-rail">
                        <div className="tl-dot">
                          {s.state === 'done' ? (
                            <Icon name="check" size={12} strokeWidth={3} />
                          ) : s.state === 'error' ? (
                            <Icon name="alert" size={12} strokeWidth={2.5} />
                          ) : s.state === 'skipped' ? (
                            '–'
                          ) : (
                            i + 1
                          )}
                        </div>
                        {i < steps.length - 1 && <div className="tl-line" />}
                      </div>
                      <div className="tl-body">
                        <div className="tl-title">
                          {s.title}
                          {s.state === 'running' && (
                            <span className="badge badge-brand">
                              <Icon name="zap" size={10} />
                              进行中
                            </span>
                          )}
                          {s.state === 'skipped' && <span className="badge badge-neutral">已跳过</span>}
                        </div>
                        <div className="tl-desc">{s.desc}</div>
                        {s.detail && (
                          <div className="tl-detail">
                            <Icon name="activity" size={12} />
                            {s.detail}
                          </div>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              )}

              {job?.status === 'done' && (
                <button className="btn btn-primary btn-block" style={{ marginTop: 6 }} onClick={onViewResult}>
                  <Icon name="eye" size={15} />
                  查看生成结果
                </button>
              )}
              {hasError && job?.error && (
                <div className="notice" style={{ marginTop: 12 }}>
                  <Icon name="alert" size={14} />
                  <span>{job.error}</span>
                </div>
              )}
            </div>
          </div>

        </div>

        {/* ---------- 右：终端日志（默认收起，点击标题栏展开） ---------- */}
        <div className="stack">
        <div className="section-card">
          <div
            className="section-head"
            style={{ cursor: 'pointer', userSelect: 'none' }}
            onClick={() => setLogOpen((v) => !v)}
            title={logOpen ? '收起运行日志' : '展开运行日志'}
          >
            <span className="section-idx">
              <Icon name="terminal" size={13} />
            </span>
            <span className="section-title">运行日志</span>
            <span className="section-desc">
              {(job?.logs ?? []).length} 条 · {logOpen ? '点击收起' : '点击展开'}
            </span>
            <Icon name={logOpen ? 'chevronUp' : 'chevronDown'} size={14} />
          </div>
          {logOpen && (
          <div className="section-body">
            <div className="terminal">
              <div className="terminal-bar">
                <span className="tdot" style={{ background: '#ff5f57' }} />
                <span className="tdot" style={{ background: '#febc2e' }} />
                <span className="tdot" style={{ background: '#28c840' }} />
                <span className="terminal-title">crosslist-agent · event stream</span>
              </div>
              <AutoScroll className="terminal-body" dep={(job?.logs ?? []).length}>
                {(job?.logs ?? []).slice(-120).map((l, i) => (
                  <div className={`row ${l.level}`} key={i}>
                    <span className="t">{timeFmt(l.ts)}</span>
                    <span className="lv">{l.level.toUpperCase()}</span>
                    <span style={{ wordBreak: 'break-word' }}>{l.msg}</span>
                  </div>
                ))}
                {(job?.logs ?? []).length === 0 && (
                  <div style={{ color: '#4d5769' }}>等待事件流…</div>
                )}
              </AutoScroll>
            </div>
          </div>
          )}
        </div>

          {/* 实时产出 */}
          <div className="section-card">
            <div className="section-head">
              <span className="section-idx">
                <Icon name="sparkles" size={13} />
              </span>
              <span className="section-title">实时产出</span>
              <span className="section-desc">边生成边预览</span>
            </div>
            <div className="section-body">
              <Segmented<PaneKey>
                value={pane}
                onChange={setPane}
                options={[
                  { value: 'card', label: '知识卡片', count: card ? 1 : 0 },
                  { value: 'listing', label: 'Listing', count: listings.length },
                  { value: 'image', label: '素材图', count: images.length },
                ]}
              />

              <div style={{ marginTop: 14 }}>
                {pane === 'card' &&
                  (card ? (
                    <div>
                      <div className="row wrap" style={{ marginBottom: 10 }}>
                        <span className="badge badge-brand">{card.category || '未分类'}</span>
                        <span className="strong t-md">{card.product_name_zh}</span>
                        <span className="muted t-sm">{card.product_name_en}</span>
                      </div>
                      <div className="check-list">
                        {(card.core_selling_points ?? []).map((p, i) => (
                          <div className="check-item" key={i}>
                            <span className="check-ic">
                              <Icon name="check" size={12} strokeWidth={2.6} />
                            </span>
                            <span>{p}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <Empty icon="box" title="等待解析产出…" desc="商品知识卡片将在解析节点后生成" />
                  ))}

                {pane === 'listing' &&
                  (listings.length ? (
                    <div className="stack" style={{ gap: 10 }}>
                      {listings.map((l, i) => (
                        <div className="listing-card" key={i}>
                          <div className="lc-head">
                            <span className="badge badge-ai">
                              {l.meta?.flag} {l.meta?.language}
                            </span>
                          </div>
                          <div className="lc-body">
                            <div className="lc-title">{l.title}</div>
                            <div className="lc-bullets" style={{ marginTop: 8 }}>
                              {(l.bullet_points ?? []).slice(0, 3).map((b, j) => (
                                <div className="lc-bullet" key={j}>
                                  <span className="mk">{j + 1}</span>
                                  <span>{b}</span>
                                </div>
                              ))}
                            </div>
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <Empty
                      icon="globe"
                      title="等待多语言生成…"
                      desc="每个目标市场将生成一套本地化 Listing"
                    />
                  ))}

                {pane === 'image' &&
                  (images.length ? (
                    <>
                      <div className="row" style={{ marginBottom: 10 }}>
                        <span className="badge badge-ok">成功 {imageOk}</span>
                        <span className="badge badge-neutral">共 {images.length}</span>
                      </div>
                      <div className="gallery">
                        {images.map((im) => (
                          <div className="shot" key={im.name} style={{ cursor: 'default' }}>
                            {/* eslint-disable-next-line @next/next/no-img-element */}
                            <img src={im.url} alt={im.label} loading="lazy" />
                            <div className="lb">{im.label}</div>
                            {!im.ok && <span className="badge">占位图</span>}
                          </div>
                        ))}
                      </div>
                    </>
                  ) : (
                    <Empty
                      icon="image"
                      title="尚未产出图片"
                      desc="图像节点将在 Listing 完成后执行，或已在输入页关闭"
                    />
                  ))}
              </div>
            </div>
          </div>
        </div>
      </div>

    </section>
  )
}
