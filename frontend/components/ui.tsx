'use client'

import { useEffect, useRef, type ReactNode } from 'react'

/* ============================================================
   轻量图标集（lucide 风格 stroke 图标，1.75 描边，24 视框）
   ============================================================ */
const PATHS: Record<string, ReactNode> = {
  upload: (
    <>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="m17 8-5-5-5 5" />
      <path d="M12 3v12" />
    </>
  ),
  plus: (
    <>
      <path d="M12 5v14" />
      <path d="M5 12h14" />
    </>
  ),
  image: (
    <>
      <rect width="18" height="18" x="3" y="3" rx="2" />
      <circle cx="9" cy="9" r="2" />
      <path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21" />
    </>
  ),
  sparkles: (
    <>
      <path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3Z" />
      <path d="M5 3v4M19 17v4M3 5h4M17 19h4" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 1 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 1 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.65 1.65 0 0 0 9 4.6a1.65 1.65 0 0 0 1-1.51V3a2 2 0 1 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 1 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z" />
    </>
  ),
  history: (
    <>
      <path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8" />
      <path d="M3 3v5h5" />
      <path d="M12 7v5l4 2" />
    </>
  ),
  check: <path d="M20 6 9 17l-5-5" />,
  checkCircle: (
    <>
      <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
      <path d="m9 11 3 3L22 4" />
    </>
  ),
  alert: (
    <>
      <path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z" />
      <path d="M12 9v4M12 17h.01" />
    </>
  ),
  info: (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 16v-4M12 8h.01" />
    </>
  ),
  x: <path d="M18 6 6 18M6 6l12 12" />,
  chevronRight: <path d="m9 18 6-6-6-6" />,
  chevronLeft: <path d="m15 18-6-6 6-6" />,
  chevronDown: <path d="m6 9 6 6 6-6" />,
  chevronUp: <path d="m18 15-6-6-6 6" />,
  download: (
    <>
      <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
      <path d="m7 10 5 5 5-5" />
      <path d="M12 15V3" />
    </>
  ),
  copy: (
    <>
      <rect width="14" height="14" x="8" y="8" rx="2" />
      <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
    </>
  ),
  globe: (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20" />
      <path d="M2 12h20" />
    </>
  ),
  box: (
    <>
      <path d="m21 8-9-5-9 5v8l9 5 9-5V8Z" />
      <path d="m3 8 9 5 9-5M12 13v10" />
    </>
  ),
  file: (
    <>
      <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
      <path d="M14 2v5h6" />
    </>
  ),
  fileText: (
    <>
      <path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z" />
      <path d="M14 2v5h6M9 13h6M9 17h6" />
    </>
  ),
  sheet: (
    <>
      <rect width="18" height="18" x="3" y="3" rx="2" />
      <path d="M3 9h18M3 15h18M9 3v18M15 3v18" />
    </>
  ),
  activity: <path d="M22 12h-4l-3 9L9 3l-3 9H2" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="10" />
      <path d="M12 6v6l4 2" />
    </>
  ),
  zap: <path d="M4 14h6v8l10-12h-6V2Z" />,
  shield: (
    <>
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z" />
      <path d="m9 12 2 2 4-4" />
    </>
  ),
  refresh: (
    <>
      <path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8" />
      <path d="M21 3v5h-5" />
      <path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16" />
      <path d="M8 16H3v5" />
    </>
  ),
  play: <path d="m6 3 14 9-14 9V3Z" />,
  layers: (
    <>
      <path d="m12 2 9 5-9 5-9-5 9-5Z" />
      <path d="m3 12 9 5 9-5M3 17l9 5 9-5" />
    </>
  ),
  target: (
    <>
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="12" r="6" />
      <circle cx="12" cy="12" r="2" />
    </>
  ),
  cpu: (
    <>
      <rect width="16" height="16" x="4" y="4" rx="2" />
      <path d="M9 9h6v6H9zM9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3" />
    </>
  ),
  eye: (
    <>
      <path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7Z" />
      <circle cx="12" cy="12" r="3" />
    </>
  ),
  maximize: <path d="M8 3H5a2 2 0 0 0-2 2v3m13-5h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3m13 5h3a2 2 0 0 0 2-2v-3" />,
  zoomIn: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m21 21-4.3-4.3M11 8v6M8 11h6" />
    </>
  ),
  zoomOut: (
    <>
      <circle cx="11" cy="11" r="7" />
      <path d="m21 21-4.3-4.3M8 11h6" />
    </>
  ),
  wand: (
    <>
      <path d="m3 21 9-9M15 4V2M15 16v-2M8 9h2M20 9h2M17.8 11.8 19 13M15 9h0M17.8 6.2 19 5" />
      <path d="m3 21 3-1 8-8-2-2-8 8Z" />
    </>
  ),
  terminal: (
    <>
      <path d="m4 17 6-6-6-6" />
      <path d="M12 19h8" />
    </>
  ),
  tag: (
    <>
      <path d="M12.6 2.6 21 11l-9.5 9.5a2 2 0 0 1-2.8 0l-7.2-7.2a2 2 0 0 1 0-2.8l9.3-9.3a2 2 0 0 1 1.8-.6Z" />
      <path d="M16.5 7.5h.01" />
    </>
  ),
  users: (
    <>
      <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
      <circle cx="9" cy="7" r="4" />
      <path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
    </>
  ),
  truck: (
    <>
      <path d="M10 17h4V5H2v12h3M20 17h2v-3.3a2 2 0 0 0-.4-1.2l-3-4a2 2 0 0 0-1.6-.8H14v9h2" />
      <circle cx="7.5" cy="17.5" r="2.5" />
      <circle cx="18.5" cy="17.5" r="2.5" />
    </>
  ),
  package: (
    <>
      <path d="m7.5 4.3 9 5.2M16.5 4.3l-9 5.2" />
      <path d="M21 8v8l-9 5-9-5V8l9-5 9 5Z" />
      <path d="M3.3 7.3 12 12l8.7-4.7M12 22V12" />
    </>
  ),
  ruler: (
    <>
      <path d="M21.3 15.3a2 2 0 0 1 0 2.8l-3.2 3.2a2 2 0 0 1-2.8 0L2.7 8.7a2 2 0 0 1 0-2.8l3.2-3.2a2 2 0 0 1 2.8 0Z" />
      <path d="m7.5 4.5 3 3M12.5 9.5l3 3M4.5 11.5l3 3" />
    </>
  ),
}

export type IconName = keyof typeof PATHS

export function Icon({
  name,
  size = 16,
  className = '',
  strokeWidth = 1.75,
}: {
  name: IconName
  size?: number
  className?: string
  strokeWidth?: number
}) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
      aria-hidden="true"
      style={{ flexShrink: 0 }}
    >
      {PATHS[name] ?? PATHS.info}
    </svg>
  )
}

/* ============================================================
   评分环
   ============================================================ */
export function ScoreRing({
  value,
  size = 52,
  thickness = 5,
  label = '分',
  muted = false,
}: {
  value: number
  size?: number
  thickness?: number
  label?: string
  muted?: boolean
}) {
  const v = Math.max(0, Math.min(100, Math.round(value || 0)))
  const r = (size - thickness) / 2
  const c = 2 * Math.PI * r
  const color = muted
    ? 'var(--ink-300)'
    : v >= 85
      ? 'var(--ok-500)'
      : v >= 70
        ? 'var(--brand-500)'
        : 'var(--err-500)'
  return (
    <div className="ring" style={{ width: size, height: size }}>
      <svg width={size} height={size}>
        <circle className="ring-bg" cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={thickness} />
        <circle
          className="ring-fg"
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={thickness}
          strokeDasharray={c}
          strokeDashoffset={c - (c * v) / 100}
        />
      </svg>
      <div className="ring-txt">
        <div>
          {v}
          <small>{label}</small>
        </div>
      </div>
    </div>
  )
}

/* ============================================================
   Modal 外壳：统一遮罩 / Esc / 滚动锁 / 底部操作区
   ============================================================ */
export function Modal({
  open,
  title,
  subtitle,
  icon,
  width = 640,
  onClose,
  footer,
  children,
}: {
  open: boolean
  title: string
  subtitle?: string
  icon?: IconName
  width?: number
  onClose: () => void
  footer?: ReactNode
  children: ReactNode
}) {
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    document.addEventListener('keydown', onKey)
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKey)
      document.body.style.overflow = prev
    }
  }, [open, onClose])

  if (!open) return null
  return (
    <div
      className="modal-mask"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose()
      }}
    >
      <div className="modal" style={{ width }}>
        <div className="modal-head">
          <div>
            <div className="modal-title">
              {icon && <Icon name={icon} size={17} />}
              {title}
            </div>
            {subtitle && <div className="modal-sub">{subtitle}</div>}
          </div>
          <button className="modal-close" onClick={onClose} aria-label="关闭">
            <Icon name="x" size={15} />
          </button>
        </div>
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-foot">{footer}</div>}
      </div>
    </div>
  )
}

/* ============================================================
   Toast 容器
   ============================================================ */
export interface ToastItem {
  id: number
  msg: string
  kind: 'info' | 'ok' | 'err'
}

export function ToastStack({ items }: { items: ToastItem[] }) {
  return (
    <div className="toast-wrap">
      {items.map((t) => (
        <div key={t.id} className={`toast ${t.kind === 'info' ? '' : t.kind}`}>
          <Icon
            name={t.kind === 'ok' ? 'checkCircle' : t.kind === 'err' ? 'alert' : 'info'}
            size={15}
          />
          {t.msg}
        </div>
      ))}
    </div>
  )
}

/* ============================================================
   空状态 / 骨架屏
   ============================================================ */
export function Empty({
  icon = 'box',
  title,
  desc,
  action,
}: {
  icon?: IconName
  title: string
  desc?: string
  action?: ReactNode
}) {
  return (
    <div className="empty">
      <div className="empty-ic">
        <Icon name={icon} size={24} />
      </div>
      <div className="empty-t">{title}</div>
      {desc && <div className="empty-d">{desc}</div>}
      {action}
    </div>
  )
}

export function Skeleton({ h = 14, w = '100%', r }: { h?: number; w?: number | string; r?: number }) {
  return <div className="sk" style={{ height: h, width: w, borderRadius: r ?? 6 }} />
}

/* ============================================================
   分段控制器
   ============================================================ */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T
  options: { value: T; label: string; count?: number }[]
  onChange: (v: T) => void
}) {
  return (
    <div className="segmented">
      {options.map((o) => (
        <button
          key={o.value}
          className={`seg${o.value === value ? ' active' : ''}`}
          onClick={() => onChange(o.value)}
        >
          {o.label}
          {o.count != null && <span style={{ opacity: 0.55, marginLeft: 5 }}>{o.count}</span>}
        </button>
      ))}
    </div>
  )
}

/* ============================================================
   自动滚动容器（日志 / 流式内容）
   ============================================================ */
export function AutoScroll({
  dep,
  className,
  children,
}: {
  dep: unknown
  className?: string
  children: ReactNode
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    // 贴近底部时才自动跟随，避免打断用户手动上翻
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 120
    if (nearBottom) el.scrollTop = el.scrollHeight
  }, [dep])
  return (
    <div ref={ref} className={className}>
      {children}
    </div>
  )
}

/* ============================================================
   平台品牌色 & Logo 字标
   ============================================================ */
export const PLATFORM_BRAND: Record<string, { bg: string; fg: string; mark: string }> = {
  amazon: { bg: 'linear-gradient(135deg,#232f3e,#131921)', fg: '#fff', mark: 'amz' },
  aliexpress: { bg: 'linear-gradient(135deg,#ff4747,#e62e04)', fg: '#fff', mark: 'AE' },
  shopee: { bg: 'linear-gradient(135deg,#ff6a1f,#ee4d2d)', fg: '#fff', mark: 'S' },
  tiktok: { bg: 'linear-gradient(135deg,#25f4ee,#000 60%)', fg: '#fff', mark: '♪' },
  lazada: { bg: 'linear-gradient(135deg,#1a3ab8,#0f2a9c)', fg: '#fff', mark: 'L' },
  temu: { bg: 'linear-gradient(135deg,#fb7701,#e35a1a)', fg: '#fff', mark: 'T' },
}

export function brandOf(key: string) {
  return (
    PLATFORM_BRAND[key] ?? {
      bg: 'linear-gradient(135deg,#475466,#2b3448)',
      fg: '#fff',
      mark: (key || '?').slice(0, 2).toUpperCase(),
    }
  )
}

/* ============================================================
   语言纯度高亮：把 Listing 中"非目标语言"的字符标红
   与后端 rules/platforms.py 的判定保持一致（日语放行汉字）
   ============================================================ */
const ALLOWED_SCRIPTS: Record<string, string[]> = {
  en: ['latin'],
  de: ['latin'],
  es: ['latin'],
  pt: ['latin'],
  fr: ['latin'],
  ko: ['hangul', 'latin'],
  ja: ['kana', 'han', 'latin'],
}

function scriptOf(cp: number): string {
  if (cp < 0x80) return 'latin'
  if ((cp >= 0x00a0 && cp <= 0x024f) || (cp >= 0x1e00 && cp <= 0x1eff) || (cp >= 0xff00 && cp <= 0xffef)) return 'latin'
  if (cp >= 0x0370 && cp <= 0x03ff) return 'greek'
  if (cp >= 0x0400 && cp <= 0x04ff) return 'cyrillic'
  if ((cp >= 0x1100 && cp <= 0x11ff) || (cp >= 0x3130 && cp <= 0x318f) || (cp >= 0xa960 && cp <= 0xa97f) || (cp >= 0xac00 && cp <= 0xd7a3)) return 'hangul'
  if ((cp >= 0x3040 && cp <= 0x30ff) || (cp >= 0x31f0 && cp <= 0x31ff)) return 'kana'
  if ((cp >= 0x3400 && cp <= 0x4dbf) || (cp >= 0x4e00 && cp <= 0x9fff) || (cp >= 0xf900 && cp <= 0xfaff)) return 'han'
  return 'other'
}

function isForeign(ch: string, langCode: string): boolean {
  const allowed = ALLOWED_SCRIPTS[langCode]
  if (!allowed) return false
  if (/\s|\d/.test(ch)) return false
  const cp = ch.codePointAt(0) ?? 0
  const s = scriptOf(cp)
  if (allowed.includes(s)) return false
  // 通用/全角标点、箭头、emoji 均非文字系统，不构成语种串台
  if (
    s === 'other' &&
    ((cp >= 0x0300 && cp <= 0x036f) ||
      (cp >= 0x2000 && cp <= 0x303f) ||
      (cp >= 0x2190 && cp <= 0x21ff) ||
      (cp >= 0x2600 && cp <= 0x27bf) ||
      (cp >= 0x2b00 && cp <= 0x2bff) ||
      (cp >= 0x1f000 && cp <= 0x1faff) ||
      cp === 0xfe0f ||
      cp === 0x20e3)
  )
    return false
  return true
}

/** 渲染文本并把非目标语言字符包进 <mark className="fx-foreign"> */
export function LangText({ text, langCode }: { text: string; langCode: string }) {
  const s = String(text ?? '')
  if (!langCode || !ALLOWED_SCRIPTS[langCode]) return <>{s}</>
  const nodes: ReactNode[] = []
  let buf = ''
  let bufForeign = false
  let k = 0
  const flush = () => {
    if (!buf) return
    nodes.push(
      bufForeign ? (
        <mark className="fx-foreign" key={k++} title="非目标语言字符">
          {buf}
        </mark>
      ) : (
        <span key={k++}>{buf}</span>
      ),
    )
    buf = ''
  }
  for (const ch of s) {
    const f = isForeign(ch, langCode)
    if (f !== bufForeign) {
      flush()
      bufForeign = f
    }
    buf += ch
  }
  flush()
  return <>{nodes}</>
}
