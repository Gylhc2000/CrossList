'use client'

import { useMemo, useRef, useState, type DragEvent } from 'react'
import { Icon, brandOf } from '@/components/ui'
import type { MetaResponse } from '@/lib/types'

export interface StartPayload {
  productName: string
  category: string
  description: string
  specs: string
  brand: string
  price: number | null
  currency: string
  platforms: string[]
  markets: string[]
  platformMarkets: Record<string, string[]>
  images: string[]
  withImages: boolean
}

interface Props {
  meta: MetaResponse | null
  starting: boolean
  onStart: (p: StartPayload) => void
  onOpenConfig: () => void
}

const IMAGE_TOTAL = 9
const DESC_MAX = 1200
const DESC_SOFT_MIN = 20
const IMG_MAX_SIDE = 1600
const IMG_KEEP_BYTES = 400 * 1024 // 小于 400KB 保留原图
const CURRENCIES = ['USD', 'EUR', 'GBP', 'CAD', 'AUD']

/**
 * 模型最容易"编造"的六类事实 —— 实测生成的 Listing 里凭空出现了
 * 「0.2lb 重量」「附赠 2 节 AAA 电池」「含测试探针」这类卡片里根本没有的信息。
 * 根因是提示词要求写满卖点，而这些字段又没有依据，模型只能补全。
 * 这里把它们显式提示出来，引导用户把事实补进规格/描述，从源头减少编造。
 * 正则只做"有没有提到"的宽松判断，用于给反馈，不参与校验。
 */
const KEY_FACTS: { label: string; tip: string; pat: RegExp }[] = [
  { label: '尺寸', tip: '长×宽×高', pat: /尺寸|长[×x*]宽|\d\s?(cm|mm|inch|in)\b/i },
  { label: '重量', tip: '整机重量', pat: /重量|克重|\d\s?(g|kg|lb|oz)\b/i },
  {
    label: '材质',
    tip: '外壳/接触材质',
    pat: /材质|材料|ABS|PC\b|硅胶|金属|不锈钢|铝合金|塑料|皮革|织物|聚酯|尼龙/i,
  },
  { label: '供电/电池', tip: '电池容量或供电方式', pat: /电池|mAh|毫安|充电|供电|续航|USB|Type-?C|AAA|锂/i },
  { label: '包装清单', tip: '盒内都有什么', pat: /包装|清单|配件|附赠|随附|包含|内含|套装|说明书|数据线/i },
  { label: '认证', tip: '有则填，没有留空', pat: /认证|CE\b|FCC|RoHS|UL\b|3C\b|质检|检测报告/i },
]

/** 压缩实拍图：长边 ≤ IMG_MAX_SIDE、JPEG 85%（小图保留原图），避免多张大原图撑爆请求体 */
function compressImage(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(reader.error)
    reader.onload = () => {
      const src = String(reader.result)
      const img = new window.Image()
      img.onerror = () => resolve(src)
      img.onload = () => {
        const scale = Math.min(1, IMG_MAX_SIDE / Math.max(img.width, img.height))
        if (scale >= 1 && file.size < IMG_KEEP_BYTES) return resolve(src)
        const canvas = document.createElement('canvas')
        canvas.width = Math.max(1, Math.round(img.width * scale))
        canvas.height = Math.max(1, Math.round(img.height * scale))
        const ctx = canvas.getContext('2d')
        if (!ctx) return resolve(src)
        ctx.fillStyle = '#ffffff'
        ctx.fillRect(0, 0, canvas.width, canvas.height)
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
        resolve(canvas.toDataURL('image/jpeg', 0.85))
      }
      img.src = src
    }
    reader.readAsDataURL(file)
  })
}

export default function InputView({ meta, starting, onStart, onOpenConfig }: Props) {
  const [productName, setProductName] = useState('')
  const [category, setCategory] = useState('')
  const [description, setDescription] = useState('')
  const [specs, setSpecs] = useState('')
  const [brand, setBrand] = useState('')
  const [price, setPrice] = useState('')
  const [currency, setCurrency] = useState('USD')
  const [platforms, setPlatforms] = useState<string[]>(['amazon', 'aliexpress', 'shopee'])
  // 每个平台各自勾选的目标市场（平台 × 市场），默认取平台默认市场
  const [platformMarkets, setPlatformMarkets] = useState<Record<string, string[]>>({})
  const [images, setImages] = useState<{ name: string; dataUrl: string }[]>([])
  const [withImages, setWithImages] = useState(true)
  const [dragOver, setDragOver] = useState(false)
  const dragDepth = useRef(0)
  const [imgWarn, setImgWarn] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)
  const specsRef = useRef<HTMLTextAreaElement>(null)

  const toggle = (list: string[], set: (v: string[]) => void, key: string) => {
    set(list.includes(key) ? list.filter((x) => x !== key) : [...list, key])
  }

  // 平台与平台×市场选择保持同步：新增平台预勾其默认市场，取消平台清空其勾选
  const togglePlatform = (pk: string) => {
    if (platforms.includes(pk)) {
      setPlatforms(platforms.filter((x) => x !== pk))
      setPlatformMarkets((pm) => {
        if (!(pk in pm)) return pm
        const { [pk]: _drop, ...rest } = pm
        return rest
      })
    } else {
      setPlatforms([...platforms, pk])
      const def = meta?.platforms.find((x) => x.key === pk)?.defaultMarket ?? 'us'
      setPlatformMarkets((pm) => (pk in pm ? pm : { ...pm, [pk]: [def] }))
    }
  }

  // 平台默认市场：元数据里约定的主站点（元数据未就绪时兜底 us）
  const defaultMarketOf = (pk: string) =>
    meta?.platforms.find((x) => x.key === pk)?.defaultMarket ?? 'us'

  // 勾/取消某平台的某个市场；允许取消到 0 个 = 该平台本次不产出（提交时自动剔除）
  const toggleUnitMarket = (pk: string, mk: string) => {
    setPlatformMarkets((pm) => {
      // 关键：首次点击时 pm[pk] 还没写入，而界面上这个平台的默认市场已经渲染成「已勾选」。
      // 若这里用 [] 当基线，第一次点任何非默认市场都会把默认勾选悄悄抹掉
      // （表现为「第一次选市场，默认的那个被取消了」）。基线必须与显示态一致。
      const cur = pm[pk] ?? [defaultMarketOf(pk)]
      const next = cur.includes(mk) ? cur.filter((x) => x !== mk) : [...cur, mk]
      return { ...pm, [pk]: next }
    })
  }

  // 展开为任务单元：market key 列表（提交时后端再按 (平台,市场) 展开为 listing 单元）。
  // 键不存在（用户未动过）→ 兜底平台默认市场；显式空数组（全部取消）→ 该平台 0 产出
  const activePlatforms = platforms.filter(
    (pk) => (platformMarkets[pk] ?? [defaultMarketOf(pk)]).length > 0,
  )
  const selMarkets = activePlatforms.flatMap((pk) => platformMarkets[pk] ?? [defaultMarketOf(pk)])
  const unitCount = selMarkets.length
  const langCovered = new Set(selMarkets).size

  const handleFiles = (files: FileList | null) => {
    if (!files) return
    const list = Array.from(files)
    const imgs = list.filter((f) => f.type.startsWith('image/'))   // 非图片文件一律拒绝
    const rejected = list.length - imgs.length
    const slots = Math.max(0, 5 - images.length)
    const taking = imgs.slice(0, slots)
    const overflow = imgs.length - taking.length

    let msg = ''
    if (rejected > 0) msg = `已忽略 ${rejected} 个非图片文件`
    if (overflow > 0) msg = msg ? `${msg}；最多 5 张，超出 ${overflow} 张未添加` : `最多 5 张，超出 ${overflow} 张未添加`
    if (msg) {
      setImgWarn(msg)
      window.setTimeout(() => setImgWarn(''), 3000)
    }

    taking.forEach((f) => {
      compressImage(f)
        .then((dataUrl) =>
          setImages((prev) => (prev.length >= 5 ? prev : [...prev, { name: f.name, dataUrl }])),
        )
        .catch(() => {})
    })
  }

  // 拖拽挂在整张卡片上：拖拽计数器防止在子元素间穿梭时误判 leave
  const onCardDragEnter = (e: DragEvent) => {
    e.preventDefault()
    dragDepth.current += 1
    setDragOver(true)
  }
  const onCardDragLeave = () => {
    dragDepth.current = Math.max(0, dragDepth.current - 1)
    if (dragDepth.current === 0) setDragOver(false)
  }
  const onCardDrop = (e: DragEvent) => {
    e.preventDefault()
    dragDepth.current = 0
    setDragOver(false)
    handleFiles(e.dataTransfer.files)
  }

  const platformList = meta?.platforms ?? []
  const marketList = meta?.markets ?? []
  const descOverflow = description.length > DESC_MAX
  const descThin = description.trim().length < DESC_SOFT_MIN
  const priceNum = price.trim() === '' ? null : Number(price)

  // 关键事实覆盖度：规格 + 描述一起判断（用户在哪儿写了都算）
  const factsText = `${specs}\n${description}`
  const factsMissing = KEY_FACTS.filter((f) => !f.pat.test(factsText))
  const factsCovered = KEY_FACTS.length - factsMissing.length
  const factsThin = factsMissing.length >= 3

  const insertFact = (label: string) => {
    setSpecs((s) => (s.trim() ? `${s.replace(/\s+$/, '')}\n${label}：` : `${label}：`))
    window.setTimeout(() => specsRef.current?.focus(), 0)
  }

  const valid = useMemo(
    () =>
      productName.trim().length > 0 &&
      activePlatforms.length > 0 &&
      !descOverflow,
    [productName, activePlatforms.length, descOverflow],
  )

  const submit = () => {
    if (!valid || starting) return
    // 只提交有产出的平台；每平台的市场取实际勾选（未动过的键兜底默认市场）
    const pm: Record<string, string[]> = {}
    activePlatforms.forEach((pk) => {
      pm[pk] = platformMarkets[pk] ?? [defaultMarketOf(pk)]
    })
    onStart({
      productName: productName.trim(),
      category: category.trim(),
      description: description.trim(),
      specs: specs.trim(),
      brand: brand.trim(),
      price: priceNum !== null && Number.isFinite(priceNum) && priceNum > 0 ? priceNum : null,
      currency,
      platforms: activePlatforms,
      markets: Array.from(new Set(selMarkets)),
      platformMarkets: pm,
      images: images.map((i) => i.dataUrl),
      withImages,
    })
  }

  return (
    <section className="home-compact">
      <div className="page-head">
        <div>
          <h1 className="page-title">新建上架任务</h1>
          <p className="page-sub">
            填写一次商品信息，自动生成多平台合规 Listing、商品主图与可直接导入后台的批量上传模板。
          </p>
        </div>
      </div>

      <div className="split triple">
        {/* ---------------- 列 1：商品原始信息 ---------------- */}
        <div className="section-card">
          <div className="section-head">
            <span className="section-idx">1</span>
            <span className="section-title">商品原始信息</span>
            <span className="section-desc">中文即可，Agent 负责本地化</span>
          </div>
          <div className="section-body">
            <div className="form-grid c2">
              <div className="field">
                <label className="form-label">
                  <span>
                    商品名称 <span className="req">*</span>
                  </span>
                  <span className="counter">{productName.length}/80</span>
                </label>
                <input
                  className="input"
                  value={productName}
                  maxLength={80}
                  placeholder="如：无线蓝牙降噪耳机 Pro Max"
                  onChange={(e) => setProductName(e.target.value)}
                />
              </div>
              <div className="field">
                <label className="form-label">
                  <span>商品品类</span>
                  <span className="counter">{category.length}/30</span>
                </label>
                <input
                  className="input"
                  value={category}
                  maxLength={30}
                  placeholder="如：3C数码 / 耳机"
                  onChange={(e) => setCategory(e.target.value)}
                />
              </div>
            </div>

            <div className="form-grid c2" style={{ marginTop: 10 }}>
              <div className="field">
                <label className="form-label">
                  <span>品牌</span>
                  <span className="counter">{brand.length}/40</span>
                </label>
                <input
                  className="input"
                  value={brand}
                  maxLength={40}
                  placeholder="如：Anker"
                  onChange={(e) => setBrand(e.target.value)}
                />
              </div>
              <div className="field">
                <label className="form-label">
                  <span>参考售价</span>
                  <span className="counter">{price.trim() === '' ? '选填' : currency}</span>
                </label>
                <div className="price-row">
                  <select
                    className="input price-currency"
                    value={currency}
                    onChange={(e) => setCurrency(e.target.value)}
                    aria-label="币种"
                  >
                    {CURRENCIES.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                  <input
                    className="input"
                    type="number"
                    min={0}
                    step={0.01}
                    value={price}
                    placeholder="如 39.99"
                    onChange={(e) => setPrice(e.target.value)}
                  />
                </div>
              </div>
            </div>

            <div className="form-grid" style={{ marginTop: 10 }}>
              <div className="field">
                <label className="form-label">
                  <span>
                    商品描述 <span className="opt-tag">建议填写</span>
                  </span>
                  <span className={`counter${descOverflow ? ' over' : ''}`}>
                    {description.length}/{DESC_MAX}
                  </span>
                </label>
                <textarea
                  className="textarea"
                  value={description}
                  rows={2}
                  placeholder="粘贴供应商给的描述即可，越详细生成质量越高"
                  onChange={(e) => setDescription(e.target.value)}
                />
                <div className={`hint${descThin && !descOverflow ? ' warn' : ''}`}>
                  <Icon name={descThin && !descOverflow ? 'alert' : 'info'} size={12} />
                  {descOverflow
                    ? `已超出 ${description.length - DESC_MAX} 字，请精简后再提交`
                    : descThin
                      ? `补充商品描述（至少 ${DESC_SOFT_MIN} 字）可显著提升 Listing 与图片质量`
                      : '支持口语化、参数堆砌等任意格式，模型会自动提炼卖点'}
                </div>
              </div>
              <div className="field">
                <label className="form-label">
                  <span>规格参数</span>
                  <span className={`counter${factsThin ? ' over' : ''}`}>
                    关键信息 {factsCovered}/{KEY_FACTS.length}
                  </span>
                </label>
                <textarea
                  ref={specsRef}
                  className="textarea"
                  value={specs}
                  rows={3}
                  placeholder={
                    '直接粘贴供应商参数表即可，如：\n产品尺寸：15.2 × 8.4 × 3.1 cm\n重量：4.2g\n材质：ABS + 硅胶\n蓝牙：5.3'
                  }
                  onChange={(e) => setSpecs(e.target.value)}
                />
                {/* 这几类事实缺失时模型最爱自行编造，点一下即可插入字段名 */}
                <div className="fact-chips">
                  {KEY_FACTS.map((f) => {
                    const on = !factsMissing.includes(f)
                    return (
                      <button
                        key={f.label}
                        type="button"
                        className={`fact-chip${on ? ' on' : ''}`}
                        onClick={() => insertFact(f.label)}
                        title={on ? `${f.label}（已提供）· ${f.tip}` : `缺「${f.label}」（${f.tip}）· 点击插入`}
                      >
                        <Icon name={on ? 'check' : 'plus'} size={10} strokeWidth={2.8} />
                        {f.label}
                      </button>
                    )
                  })}
                </div>
                <div className={`hint${factsThin ? ' warn' : ''}`}>
                  <Icon name={factsThin ? 'alert' : 'ruler'} size={12} />
                  {factsMissing.length === 0
                    ? '关键信息已齐备，模型无需靠推测补全参数'
                    : `还缺 ${factsMissing.map((f) => f.label).join('、')} —— 缺项没有依据时，模型容易自行编造（例如凭空写上「附赠电池」「0.2lb 重量」）`}
                </div>
              </div>
            </div>

            <div className="switch-row" style={{ marginTop: 10 }}>
              <label className="switch">
                <input
                  type="checkbox"
                  checked={withImages}
                  onChange={(e) => setWithImages(e.target.checked)}
                />
                <span className="switch-slider" />
              </label>
              <div style={{ flex: 1 }}>
                <div className="switch-title">
                  <Icon name="wand" size={14} />
                  生成商品主图与详情页图
                  <span className="badge badge-ai">{IMAGE_TOTAL} 张</span>
                </div>
                <div className="switch-desc">关闭后仅产出 Listing 与模板文件，耗时可缩短约 60%。</div>
              </div>
            </div>
          </div>
        </div>

        {/* ---------------- 列 2：实拍图 + 目标平台 ---------------- */}
        <div className="stack">
          <div
            className={`section-card${dragOver ? ' drag-over' : ''}`}
            onDragEnter={onCardDragEnter}
            onDragOver={(e) => e.preventDefault()}
            onDragLeave={onCardDragLeave}
            onDrop={onCardDrop}
          >
            <div className="section-head">
              <span className="section-idx">2</span>
              <span className="section-title">商品实拍图</span>
              <span className="section-desc">
                {imgWarn ? (
                  <span style={{ color: 'var(--warn-600)' }}>{imgWarn}</span>
                ) : (
                  `${images.length}/5 张`
                )}
              </span>
            </div>
            <div className="section-body">
              {images.length === 0 && (
                <div className={`upload-zone${dragOver ? ' over' : ''}`} onClick={() => fileRef.current?.click()}>
                  <div className="upload-ic">
                    <Icon name="upload" size={18} />
                  </div>
                  <div className="upload-text">
                    <div className="upload-title">
                      拖拽图片到此处，或 <em>点击上传</em>
                    </div>
                    <div className="upload-hint">
                      最多 5 张 · 上传后作为素材图参考，保证生成图与实拍商品外观一致
                    </div>
                  </div>
                </div>
              )}

              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                multiple
                hidden
                onChange={(e) => handleFiles(e.target.files)}
              />

              {images.length > 0 && (
                <div className="thumb-row" style={{ justifyContent: 'flex-start', marginTop: 10 }}>
                  {images.map((im, i) => (
                    <div className="thumb" key={i}>
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      <img src={im.dataUrl} alt={im.name} />
                      <button
                        className="del"
                        aria-label="移除"
                        onClick={(e) => {
                          e.stopPropagation()
                          setImages((prev) => prev.filter((_, idx) => idx !== i))
                        }}
                      >
                        <Icon name="x" size={11} strokeWidth={2.5} />
                      </button>
                    </div>
                  ))}
                  {images.length < 5 && (
                    <button
                      className="thumb add-tile"
                      aria-label="添加图片"
                      title={`还可添加 ${5 - images.length} 张`}
                      onClick={() => fileRef.current?.click()}
                    >
                      <Icon name="plus" size={16} />
                    </button>
                  )}
                </div>
              )}

              {images.length > 0 && (
                <div className="hint" style={{ marginTop: 8 }}>
                  <Icon name="wand" size={12} />
                  已上传 {images.length} 张实拍图，将作为素材图生成的参考图：出图的商品外观、配色与细节以实拍图为准
                </div>
              )}
            </div>
          </div>

          <div className="section-card">
            <div className="section-head">
              <span className="section-idx">3</span>
              <span className="section-title">
                目标平台 <span className="req">*</span>
              </span>
              <span className="section-desc">已选 {platforms.length} 个</span>
            </div>
            <div className="section-body">
              <div className="opt-grid platforms">
                {platformList.map((p) => {
                  const b = brandOf(p.key)
                  const on = platforms.includes(p.key)
                  return (
                    <button
                      key={p.key}
                      className={`opt${on ? ' active' : ''}`}
                      onClick={() => togglePlatform(p.key)}
                      aria-pressed={on}
                    >
                      <span className="opt-check">
                        <Icon name="check" size={11} strokeWidth={3} />
                      </span>
                      <span className="opt-logo" style={{ background: b.bg, color: b.fg }}>
                        {b.mark}
                      </span>
                      <span className="opt-main">
                        <span className="opt-name">{p.name}</span>
                        <span className="opt-meta">
                          {p.imageSize} · {p.fileExt.toUpperCase()}
                        </span>
                      </span>
                    </button>
                  )
                })}
                {platformList.length === 0 && (
                  <>
                    <div className="sk" style={{ height: 48 }} />
                    <div className="sk" style={{ height: 48 }} />
                    <div className="sk" style={{ height: 48 }} />
                  </>
                )}
              </div>
            </div>
          </div>
        </div>

        {/* ---------------- 列 3：平台×市场 + 摘要 ---------------- */}
        <div className="stack">
          <div className="section-card">
            <div className="section-head">
              <span className="section-idx">4</span>
              <span className="section-title">
                平台 × 目标市场 <span className="req">*</span>
              </span>
              <span className="section-desc">{unitCount} 个站点 · 覆盖 {langCovered} 语言</span>
            </div>
            <div className="section-body">
              <div className="hint" style={{ marginTop: 0, marginBottom: 8 }}>
                <Icon name="info" size={12} />
                为每个平台勾选要上架的市场，各生成对应语言的 Listing；默认勾选该平台的主站点
              </div>
              <div className="pmatrix">
                {platformList
                  .filter((pl) => platforms.includes(pl.key))
                  .map((pl) => {
                    const b = brandOf(pl.key)
                    const chosen = platformMarkets[pl.key] ?? [defaultMarketOf(pl.key)]
                    const empty = chosen.length === 0
                    return (
                      <div
                        className={`pm-row${empty ? ' off' : ''}`}
                        key={pl.key}
                        title={empty ? '未勾选任何市场，该平台本次不产出' : undefined}
                      >
                        <div className="pm-plat">
                          <span className="opt-logo" style={{ background: b.bg, color: b.fg, width: 22, height: 22, fontSize: 9 }}>
                            {b.mark}
                          </span>
                          <span className="pm-name">{pl.name}</span>
                        </div>
                        <div className="pm-chips">
                          {marketList.map((m) => {
                            const on = chosen.includes(m.key)
                            return (
                              <button
                                key={m.key}
                                className={`mk-chip${on ? ' on' : ''}`}
                                onClick={() => toggleUnitMarket(pl.key, m.key)}
                                aria-pressed={on}
                                title={`${pl.name} × ${m.label}（${m.language}）`}
                              >
                                {m.flag}
                              </button>
                            )
                          })}
                        </div>
                        <span className={`pm-count${empty ? ' zero' : ''}`}>{empty ? '不产出' : chosen.length}</span>
                      </div>
                    )
                  })}
                {platformList.filter((pl) => platforms.includes(pl.key)).length === 0 && (
                  <div className="sk" style={{ height: 64 }} />
                )}
              </div>
            </div>
          </div>

          <div className="summary-card">
            <div className="summary-head">
              <div className="summary-head-t">
                <Icon name="layers" size={15} />
                任务摘要
              </div>
              <div className="summary-head-s">确认无误后启动 Agent</div>
            </div>

            <div className="summary-body">
              <div className="sum-row">
                <span className="sum-k">商品</span>
                <span className="sum-v" title={productName.trim() || undefined}>
                  {productName.trim() || '未填写'}
                </span>
              </div>
              <div className="sum-row">
                <span className="sum-k">品类</span>
                <span className="sum-v" title={category.trim() || undefined}>
                  {category.trim() || '—'}
                </span>
              </div>
              <div className="sum-row">
                <span className="sum-k">平台</span>
                <span
                  className="sum-v"
                  title={
                    platforms.length
                      ? platformList
                          .filter((p) => platforms.includes(p.key))
                          .map((p) => p.name)
                          .join('、')
                      : undefined
                  }
                >
                  {platforms.length
                    ? platformList
                        .filter((p) => platforms.includes(p.key))
                        .map((p) => p.name)
                        .join('、')
                    : '未选择'}
                </span>
              </div>
              <div className="sum-row">
                <span className="sum-k">参考图</span>
                <span className="sum-v">{images.length} 张</span>
              </div>
              <div className="sum-row">
                <span className="sum-k">站点版本</span>
                <span className="sum-v">
                  {unitCount} 个 · 覆盖 {langCovered} 语言
                </span>
              </div>
            </div>

            <div className="sum-foot">
              <div className="est-grid">
                <div className="est">
                  <div className="est-n">{activePlatforms.length}</div>
                  <div className="est-l">平台包</div>
                </div>
                <div className="est">
                  <div className="est-n">{unitCount}</div>
                  <div className="est-l">Listing</div>
                </div>
                <div className="est">
                  <div className="est-n">{langCovered}</div>
                  <div className="est-l">覆盖语言</div>
                </div>
                <div className="est">
                  <div className="est-n">{withImages ? IMAGE_TOTAL : 0}</div>
                  <div className="est-l">素材图</div>
                </div>
              </div>

              <button
                className="btn btn-primary btn-lg btn-block"
                onClick={submit}
                disabled={starting || !valid}
              >
                {starting ? (
                  <>
                    <span className="spinner" /> 正在创建任务…
                  </>
                ) : (
                  <>
                    <Icon name="play" size={15} strokeWidth={2.2} />
                    开始生成上架素材
                  </>
                )}
              </button>

              {/* 仅在无法生成时提示；槽位常驻占位，避免出现/消失导致下方内容跳动 */}
              <div className="notice-slot">
                {!valid ? (
                  <div className="notice info">
                    <Icon name="info" size={14} />
                    <span>
                      {!productName.trim()
                        ? '请填写商品名称'
                        : descOverflow
                          ? '商品描述超出长度限制'
                          : '请至少选择 1 个平台和市场'}
                    </span>
                  </div>
                ) : factsThin ? (
                  /* 不阻断提交：只说明代价。规格越空，模型越要靠推测补全，编造卖点就出在这里 */
                  <div className="notice info">
                    <Icon name="info" size={14} />
                    <span>
                      规格信息偏少（缺 {factsMissing.map((f) => f.label).join('、')}）：
                      生成结果可能出现与实物不符的参数，建议补充后再提交
                    </span>
                  </div>
                ) : null}
              </div>

              <div className="hint" style={{ marginTop: 8, justifyContent: 'center' }}>
                <Icon name="shield" size={12} />
                全流程自动合规校验，不达标自动修正
              </div>
            </div>
          </div>
        </div>
      </div>
    </section>
  )
}
