'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Empty, Icon, LangText, ScoreRing, Segmented, brandOf } from '@/components/ui'
import type { JobSnapshot } from '@/lib/types'

interface Props {
  job: JobSnapshot
  activeTab: string
  onTabChange: (key: string) => void
  onGoDownload: () => void
  onBackProgress: () => void
}

type GroupKey = 'all' | 'main' | 'detail'

function scoreClass(v: number) {
  return v >= 85 ? '' : v >= 70 ? 'mid' : 'low'
}

/** 脱敏图的角标悬停说明：讲清为什么脱敏、画面意味着什么 */
function maskTip(note?: string) {
  return (
    '脱敏生成：首次出图被图像模型 IP/版权风控拦截，已自动移除品牌名、角色名等 IP 特征词后重新生成。' +
    '画面只保留品类、颜色、材质等通用特征，不还原原 IP 本体形象；正式上架请替换为自有实拍图或已授权素材。' +
    (note ? `（${note}）` : '')
  )
}

export default function PreviewView({
  job,
  activeTab,
  onTabChange,
  onGoDownload,
  onBackProgress,
}: Props) {
  const [group, setGroup] = useState<GroupKey>('all')
  const [lightbox, setLightbox] = useState<number | null>(null)
  const [zoom, setZoom] = useState(1)
  const [dragging, setDragging] = useState(false)
  const bodyRef = useRef<HTMLDivElement>(null)
  const dragRef = useRef<{ x: number; y: number; sl: number; st: number } | null>(null)

  const platforms = job.result.platforms ?? []
  const p = platforms.find((x) => x.key === activeTab) ?? platforms[0]
  const card = job.result.knowledge_card
  const allImages = job.result.images ?? []
  const maskedN = allImages.filter((i) => i.ok && i.note).length
  const failedN = allImages.filter((i) => !i.ok).length

  const visibleImages = useMemo(
    () => (group === 'all' ? allImages : allImages.filter((i) => i.group === group)),
    [allImages, group],
  )

  const closeLightbox = useCallback(() => setLightbox(null), [])
  const moveLightbox = useCallback(
    (d: number) => {
      setLightbox((cur) => {
        if (cur == null || visibleImages.length === 0) return cur
        return (cur + d + visibleImages.length) % visibleImages.length
      })
    },
    [visibleImages.length],
  )

  useEffect(() => {
    if (lightbox == null) return
    setZoom(1)
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') closeLightbox()
      if (e.key === 'ArrowRight') moveLightbox(1)
      if (e.key === 'ArrowLeft') moveLightbox(-1)
      if (e.key === '+' || e.key === '=') setZoom((z) => Math.min(4, Math.round((z + 0.25) * 100) / 100))
      if (e.key === '-') setZoom((z) => Math.max(1, Math.round((z - 0.25) * 100) / 100))
    }
    // 滚轮缩放：必须用非 passive 原生监听才能阻止图区默认滚动
    const onWheel = (e: WheelEvent) => {
      e.preventDefault()
      setZoom((z) => {
        const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15
        return Math.min(4, Math.max(1, Math.round(z * factor * 100) / 100))
      })
    }
    document.addEventListener('keydown', onKey)
    const body = bodyRef.current
    body?.addEventListener('wheel', onWheel, { passive: false })
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      body?.removeEventListener('wheel', onWheel)
      document.body.style.overflow = prev
    }
  }, [lightbox, closeLightbox, moveLightbox])

  if (!p) {
    return (
      <section>
        <h1 className="page-title">生成结果预览</h1>
        <div className="section-card" style={{ marginTop: 16 }}>
          <Empty icon="box" title="暂无结果" desc="请先运行一个任务" />
        </div>
      </section>
    )
  }

  const l = p.listing ?? { title: '', bullet_points: [], description: '', search_terms: '' }
  const title = l.title ?? ''
  const bullets = l.bullet_points ?? []
  const scored = !p.quality?.failed
  const score = Number(p.quality?.score ?? 0)
  const brand = brandOf(p.pk ?? p.key)
  const titleOver = title.length > p.rules.titleMax
  const titlePct = Math.min(100, Math.round((title.length / p.rules.titleMax) * 100))

  const baseChecks = [
    {
      level: titleOver ? ('err' as const) : ('ok' as const),
      msg: `标题长度 ${title.length} 字符 / 上限 ${p.rules.titleMax}`,
    },
    {
      level: bullets.length === p.rules.bulletCount ? ('ok' as const) : ('warn' as const),
      msg: `卖点条目 ${bullets.length} 条 / 建议 ${p.rules.bulletCount} 条`,
    },
    { level: 'ok' as const, msg: `主图规范：${p.rules.mainImage}（${p.image_size}）` },
    {
      level: score >= 85 ? ('ok' as const) : score >= 70 ? ('warn' as const) : ('err' as const),
      msg: scored ? `本地化语言质量 ${score} / 100` : `本地化语言质量 —（评分未返回）`,
    },
  ]
  const extraChecks = (p.checks ?? []).map((c) => ({
    level: c.level === 'error' ? ('err' as const) : ('warn' as const),
    msg: `${c.field}：${c.msg}${c.fix ? `（建议：${c.fix}）` : ''}`,
  }))
  const checks = [...baseChecks, ...extraChecks]
  const issueCount = checks.filter((c) => c.level !== 'ok').length

  return (
    <section>
      <div className="page-head">
        <div>
          <h1 className="page-title">
            生成结果预览
            <span className="badge badge-ok">
              <Icon name="checkCircle" size={12} />
              {platforms.length} 份站点 Listing 已就绪
            </span>
          </h1>
          <p className="page-sub">
            {card?.product_name_zh || '商品'} · 共 {platforms.length} 套本地化 Listing，
            {issueCount === 0 ? '全部通过规则校验' : `${issueCount} 项需关注`}
          </p>
        </div>
        <div className="page-head-aside">
          <button className="btn btn-ghost btn-sm" onClick={onBackProgress}>
            <Icon name="chevronLeft" size={14} />
            进度
          </button>
          <button className="btn btn-primary btn-sm" onClick={onGoDownload}>
            <Icon name="download" size={14} />
            素材包下载
          </button>
        </div>
      </div>

      {/* 任务级告警：外观来源、脱敏重试、降级占位图等，直接影响结果可信度，必须让人看到 */}
      {(job.warnings ?? []).length > 0 && (
        <div className="notice multi" style={{ marginBottom: 14 }}>
          <Icon name="alert" size={14} />
          <div>
            {(job.warnings ?? []).map((w, i) => (
              <div key={i}>{w}</div>
            ))}
          </div>
        </div>
      )}

      {/* 平台切换 */}
      <div className="tabs-pill" style={{ marginBottom: 18 }}>
        {platforms.map((x) => {
          const b = brandOf(x.pk ?? x.key)
          const s = Number(x.quality?.score ?? 0)
          const sOk = !x.quality?.failed
          return (
            <button
              key={x.key}
              className={`tp${x.key === p.key ? ' active' : ''}`}
              onClick={() => onTabChange(x.key)}
            >
              <span
                className="opt-logo"
                style={{ background: b.bg, color: b.fg, width: 24, height: 24, fontSize: 10 }}
              >
                {b.mark}
              </span>
              <span className="tp-flag">{x.flag}</span>
              <span>
                <span className="tp-name">{x.name}</span>
                <br />
                <span className="tp-lang">
                  {x.market_label} · {x.language}
                </span>
              </span>
              <span className={`tp-score ${scoreClass(s)}`}>{sOk ? s : '—'}</span>
            </button>
          )
        })}
      </div>

      <div className="split results">
        {/* ---------- 左：Listing ---------- */}
        <div className="stack">
          <div className="listing-card">
            <div className="lc-head">
              <div className="row">
                <span
                  className="opt-logo"
                  style={{ background: brand.bg, color: brand.fg, width: 26, height: 26, fontSize: 11 }}
                >
                  {brand.mark}
                </span>
                <span className="strong">{p.name} Listing</span>
              </div>
              <div className="row wrap">
                <span className="badge badge-neutral">{p.market_label}</span>
                <span className="badge badge-ai">{p.language}</span>
                <span className="badge badge-neutral">主图 {p.image_size}</span>
                <span className="badge badge-neutral">{p.file_ext.toUpperCase()}</span>
              </div>
            </div>

            <div className="lc-body">
              <div className="row" style={{ justifyContent: 'space-between', marginBottom: 6 }}>
                <span className="panel-title">
                  <Icon name="tag" size={12} />
                  商品标题
                </span>
                <span className={`t-sm num ${titleOver ? '' : 'muted'}`} style={titleOver ? { color: 'var(--err-500)', fontWeight: 600 } : undefined}>
                  {title.length} / {p.rules.titleMax}
                </span>
              </div>
              <div className="lc-title">
                <LangText text={title} langCode={p.lang_code} />
              </div>
              <div className={`meter${titleOver ? ' over' : ''}`}>
                <i style={{ width: `${titlePct}%` }} />
              </div>

              <div className="panel-title" style={{ marginTop: 18 }}>
                <Icon name="sparkles" size={12} />
                核心卖点 · {bullets.length} 条
              </div>
              <div className="lc-bullets">
                {bullets.map((b, i) => (
                  <div className="lc-bullet" key={i}>
                    <span className="mk">{i + 1}</span>
                    <span>
                      <LangText text={b} langCode={p.lang_code} />
                    </span>
                  </div>
                ))}
              </div>

              <div className="lc-desc">
                <LangText text={l.description} langCode={p.lang_code} />
              </div>

              {l.search_terms && (
                <div className="lc-kw">
                  <span className="panel-title" style={{ marginBottom: 5 }}>
                    <Icon name="target" size={12} />
                    搜索关键词
                  </span>
                  <LangText text={l.search_terms} langCode={p.lang_code} />
                </div>
              )}
            </div>
          </div>

          {/* 合规校验 */}
          <div className="section-card">
            <div className="section-head">
              <span className="section-idx">
                <Icon name="shield" size={13} />
              </span>
              <span className="section-title">规则校验与质量评分</span>
              <span className="section-desc">
                {issueCount === 0 ? '全部通过' : `${issueCount} 项待优化`}
              </span>
            </div>
            <div className="section-body">
              <div className="row" style={{ alignItems: 'center', marginBottom: 14 }}>
                <ScoreRing value={score} size={58} muted={!scored} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div className="strong t-md">
                    {score >= 85 ? '本地化质量优秀' : score >= 70 ? '质量良好，有优化空间' : '建议人工复核'}
                  </div>
                  <div className="t-sm muted" style={{ marginTop: 2 }}>
                    {p.quality?.comments || '模型对语种地道性、术语准确性与平台语气的综合评分'}
                  </div>
                  {(p.quality?.suggestions ?? []).length > 0 && (
                    <div className="hint" style={{ marginTop: 6 }}>
                      <Icon name="info" size={12} />
                      {(p.quality?.suggestions ?? []).join('；')}
                    </div>
                  )}
                </div>
              </div>

              <div className="check-list">
                {checks.map((c, i) => (
                  <div className={`check-item ${c.level}`} key={i}>
                    <span className="check-ic">
                      <Icon
                        name={c.level === 'ok' ? 'checkCircle' : c.level === 'warn' ? 'alert' : 'x'}
                        size={13}
                        strokeWidth={2.4}
                      />
                    </span>
                    <span>{c.msg}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>

        {/* ---------- 右：图片 + 知识卡片 ---------- */}
        <div className="stack">
          <div className="section-card">
            <div className="section-head">
              <span className="section-idx">
                <Icon name="image" size={13} />
              </span>
              <span className="section-title">商品图片素材</span>
              <span className="section-desc">
                成功 {allImages.filter((i) => i.ok).length} / {allImages.length}
              </span>
            </div>
            <div className="section-body">
              {maskedN + failedN > 0 && (
                <div className="notice multi" style={{ marginBottom: 12 }}>
                  <Icon name="shield" size={14} />
                  <div>
                    {maskedN > 0 && (
                      <>
                        <b>{maskedN} 张为脱敏生成</b>：首次出图被图像模型 IP/版权风控拦截，
                        已自动移除品牌名、角色名等 IP 特征词后重新生成。这类图只保留品类、颜色、材质等通用特征，
                        <b>不还原原 IP 本体形象</b>，正式上架前请替换为自有实拍图或已授权素材。
                      </>
                    )}
                    {maskedN > 0 && failedN > 0 && <br />}
                    {failedN > 0 && (
                      <>
                        <b>{failedN} 张降级为占位图</b>：脱敏重试后仍被风控拦截，需人工补图。
                      </>
                    )}
                    <br />
                    鼠标悬停图片上的角标，可查看该张的具体处理方式。
                  </div>
                </div>
              )}
              {allImages.length > 0 && (
                <div className="hint" style={{ marginBottom: 12 }}>
                  <Icon name="wand" size={12} />
                  {allImages.some((i) => (i.prompt ?? '').includes('reference photo'))
                    ? '已按上传的实拍图生成：画面中的商品外观、配色与结构以实拍图为准'
                    : '未使用实拍图：本组素材按文字描述生成，商品外观为模型推断，建议上传实拍图后重跑'}
                </div>
              )}
              {allImages.length > 0 && (
                <div style={{ marginBottom: 12 }}>
                  <Segmented<GroupKey>
                    value={group}
                    onChange={setGroup}
                    options={[
                      { value: 'all', label: '全部', count: allImages.length },
                      { value: 'main', label: '主图', count: allImages.filter((i) => i.group === 'main').length },
                      {
                        value: 'detail',
                        label: '详情图',
                        count: allImages.filter((i) => i.group === 'detail').length,
                      },
                    ]}
                  />
                </div>
              )}

              {visibleImages.length ? (
                <>
                  <div className="gallery">
                    {visibleImages.map((im, i) => (
                      <div className="shot" key={im.name} onClick={() => setLightbox(i)}>
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        <img src={im.url} alt={im.label} loading="lazy" />
                        <div className="lb">{im.label}</div>
                        {!im.ok && (
                          <span className="badge" title={im.error || '生成失败'}>
                            占位图
                          </span>
                        )}
                        {im.ok && im.note && (
                          <span className="badge badge-neutral" title={maskTip(im.note)}>
                            脱敏生成
                          </span>
                        )}
                        <div className="zoom">
                          <Icon name="maximize" size={18} />
                        </div>
                      </div>
                    ))}
                  </div>
                  <div className="hint">
                    <Icon name="maximize" size={12} />
                    点击任意图片可放大查看 · 目标尺寸 {p.image_size}（{p.image_px}px）
                  </div>
                </>
              ) : (
                <Empty
                  icon="image"
                  title="本次未生成图片"
                  desc="在输入页关闭了图像生成，仅产出 Listing 与批量上传模板"
                />
              )}
            </div>
          </div>

          <div className="section-card">
            <div className="section-head">
              <span className="section-idx">
                <Icon name="box" size={13} />
              </span>
              <span className="section-title">商品知识卡片</span>
              <span className="section-desc">解析节点产出</span>
            </div>
            <div className="section-body">
              {card ? (
                <div className="stack" style={{ gap: 14 }}>
                  <div>
                    <div className="strong t-lg">{card.product_name_zh}</div>
                    <div className="t-sm muted">{card.product_name_en}</div>
                    <div className="row wrap" style={{ marginTop: 8 }}>
                      <span className="badge badge-brand">{card.category}</span>
                      {card.suggested_price?.value != null && (
                        <span
                          className={`badge ${card.price_source === 'model' ? 'badge-warn' : 'badge-neutral'}`}
                          title={card.price_source === 'model' ? '由模型按品类估计，建议核对成本与竞品后修改' : '来自用户填写的参考售价'}
                        >
                          建议售价 {card.suggested_price.currency ?? ''} {card.suggested_price.value}
                          {card.price_source === 'model' ? ' · 模型估计' : ''}
                        </span>
                      )}
                    </div>
                  </div>

                  {(card.core_selling_points ?? []).length > 0 && (
                    <div>
                      <div className="panel-title" style={{ marginBottom: 7 }}>
                        <Icon name="zap" size={12} />
                        核心卖点
                      </div>
                      <div className="check-list">
                        {(card.core_selling_points ?? []).map((x, i) => (
                          <div className="check-item" key={i}>
                            <span className="check-ic">
                              <Icon name="check" size={12} strokeWidth={2.6} />
                            </span>
                            <span>{x}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {(card.specs ?? []).length > 0 && (
                    <div>
                      <div className="panel-title" style={{ marginBottom: 5 }}>
                        <Icon name="ruler" size={12} />
                        规格参数
                      </div>
                      <div className="kv-grid">
                        {(card.specs ?? []).map((s, i) => (
                          <div className="kv" key={i}>
                            <span className="kv-k">{s.name}</span>
                            <span className="kv-v">{s.value}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="kv-grid">
                    {(card.target_audience ?? []).length > 0 && (
                      <div className="kv">
                        <span className="kv-k">
                          <Icon name="users" size={12} />
                          目标人群
                        </span>
                        <span className="kv-v">{(card.target_audience ?? []).join('、')}</span>
                      </div>
                    )}
                    {(card.usage_scenarios ?? []).length > 0 && (
                      <div className="kv">
                        <span className="kv-k">
                          <Icon name="globe" size={12} />
                          使用场景
                        </span>
                        <span className="kv-v">{(card.usage_scenarios ?? []).join('、')}</span>
                      </div>
                    )}
                    {card.package_contents && (
                      <div className="kv">
                        <span className="kv-k">
                          <Icon name="package" size={12} />
                          包装清单
                        </span>
                        <span className="kv-v">{card.package_contents}</span>
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <Empty icon="box" title="暂无知识卡片" />
              )}
            </div>
          </div>
        </div>
      </div>

      {/* 灯箱：Portal 渲染到 body，避免任何祖先 transform 影响 fixed 居中 */}
      {lightbox != null &&
        visibleImages[lightbox] &&
        createPortal(
          <div
            className="lightbox"
            onClick={(e) => {
              if (e.target === e.currentTarget) closeLightbox()
            }}
          >
          <div className="lb-card">
            <div className="lb-head">
              <span className="lb-title">{visibleImages[lightbox].label}</span>
              {visibleImages[lightbox].ok && visibleImages[lightbox].note && (
                <span className="badge badge-neutral" title={maskTip(visibleImages[lightbox].note)}>
                  脱敏生成
                </span>
              )}
              {!visibleImages[lightbox].ok && (
                <span className="badge" title={visibleImages[lightbox].error || '生成失败'}>
                  占位图
                </span>
              )}
              <span className="lb-count">
                {lightbox + 1} / {visibleImages.length}
              </span>
              <div className="spacer" />
              <button className="lb-nav lb-close" onClick={closeLightbox} aria-label="关闭">
                <Icon name="x" size={16} />
              </button>
            </div>
            <div
              className={`lb-body${zoom > 1 ? ' can-pan' : ''}${dragging ? ' dragging' : ''}`}
              ref={bodyRef}
              onMouseDown={(e) => {
                if (zoom <= 1) return
                e.preventDefault()
                const el = bodyRef.current
                if (!el) return
                dragRef.current = { x: e.clientX, y: e.clientY, sl: el.scrollLeft, st: el.scrollTop }
                setDragging(true)
              }}
              onMouseMove={(e) => {
                const d = dragRef.current
                const el = bodyRef.current
                if (!d || !el) return
                el.scrollLeft = d.sl - (e.clientX - d.x)
                el.scrollTop = d.st - (e.clientY - d.y)
              }}
              onMouseUp={() => {
                dragRef.current = null
                setDragging(false)
              }}
              onMouseLeave={() => {
                dragRef.current = null
                setDragging(false)
              }}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={visibleImages[lightbox].url}
                alt={visibleImages[lightbox].label}
                onDoubleClick={() => setZoom(1)}
                draggable={false}
                style={zoom > 1 ? { width: `${zoom * 100}%`, maxWidth: 'none', maxHeight: 'none' } : undefined}
              />
            </div>
            <div className="lb-foot">
              <button
                className="lb-nav"
                onClick={() => {
                  moveLightbox(-1)
                  setZoom(1)
                }}
                aria-label="上一张"
              >
                <Icon name="chevronLeft" size={16} />
              </button>
              <button
                className="lb-nav"
                onClick={() => {
                  moveLightbox(1)
                  setZoom(1)
                }}
                aria-label="下一张"
              >
                <Icon name="chevronRight" size={16} />
              </button>
            </div>
          </div>
        </div>,
        document.body,
      )}
    </section>
  )
}
