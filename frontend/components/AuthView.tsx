'use client'

import { useEffect, useRef, useState } from 'react'
import { ApiError, api } from '@/lib/api'
import { Icon } from '@/components/ui'
import type { AuthStatus, UserMe } from '@/lib/types'

type Mode = 'login' | 'register'

interface Props {
  onAuthed: (user: UserMe) => void
  toast?: (msg: string, kind?: 'info' | 'ok' | 'err') => void
}

const PASSWORD_MIN = 8

/**
 * 登录 / 注册。会话在 HttpOnly Cookie 里，前端拿不到凭据本身，
 * 所以这里只负责把表单交出去，401/403 的文案由后端给（它会区分邀请码与口令错误）。
 */
export default function AuthView({ onAuthed, toast }: Props) {
  const [status, setStatus] = useState<AuthStatus | null>(null)
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [invite, setInvite] = useState('')
  const [showPw, setShowPw] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const firstFieldRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let alive = true
    api
      .authStatus()
      .then((s) => {
        if (!alive) return
        setStatus(s)
        // 空实例上没有账号可登录，直接落到注册，省一次必然失败的尝试
        if (!s.hasAccounts) setMode('register')
      })
      .catch(() => {
        if (alive) setStatus({ openSignup: false, inviteRequired: true, hasAccounts: true })
      })
    return () => {
      alive = false
    }
  }, [])

  useEffect(() => {
    firstFieldRef.current?.focus()
  }, [mode])

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy) return
    const name = username.trim()
    if (!name || !password) {
      setErr('请填写用户名与口令')
      return
    }
    if (mode === 'register' && password.length < PASSWORD_MIN) {
      setErr(`口令至少 ${PASSWORD_MIN} 位`)
      return
    }
    setBusy(true)
    setErr('')
    try {
      const r =
        mode === 'login'
          ? await api.login({ username: name, password })
          : await api.register({ username: name, password, invite: invite.trim() || undefined })
      setPassword('')
      onAuthed(r.user)
    } catch (ex) {
      const e2 = ex as ApiError
      // 401 不回显"用户是否存在"，后端已经合并了文案；429 是限速，值得单独提示
      setErr(e2?.status === 429 ? e2.message : e2?.message || '登录失败，请重试')
      if (e2?.status === 429) toast?.(e2.message, 'err')
    } finally {
      setBusy(false)
    }
  }

  const switchMode = (next: Mode) => {
    setMode(next)
    setErr('')
    setPassword('')
  }

  const needInvite = mode === 'register' && status?.inviteRequired

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
          <div className="header-actions">
            <span className="key-pill ok">
              <span className="key-dot" />
              需要登录
            </span>
          </div>
        </div>
      </header>

      <main className="app-main auth-main">
        <div className="auth-card">
          <div className="auth-head">
            <div className="auth-title">{mode === 'login' ? '登录工作台' : '创建账号'}</div>
            <div className="auth-sub">
              {mode === 'login'
                ? '任务与素材包按账号保存，重启服务后记录仍在'
                : status?.openSignup
                  ? '填写用户名与口令即可开始'
                  : '本站需邀请码注册，请向管理员索取'}
            </div>
          </div>

          <div className="segmented auth-switch" role="tablist">
            <button
              type="button"
              className={`seg${mode === 'login' ? ' active' : ''}`}
              onClick={() => switchMode('login')}
              aria-selected={mode === 'login'}
            >
              登录
            </button>
            <button
              type="button"
              className={`seg${mode === 'register' ? ' active' : ''}`}
              onClick={() => switchMode('register')}
              aria-selected={mode === 'register'}
            >
              注册
            </button>
          </div>

          <form className="auth-form" onSubmit={submit} noValidate>
            <div className="field">
              <label className="form-label" htmlFor="auth-user">
                用户名<span className="req">*</span>
              </label>
              <input
                id="auth-user"
                ref={firstFieldRef}
                className="input"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                autoComplete="username"
                placeholder="2-32 位，中文/字母/数字"
                maxLength={32}
              />
            </div>

            <div className="field">
              <label className="form-label" htmlFor="auth-pw">
                口令<span className="req">*</span>
                {mode === 'register' && (
                  <span className={`counter${password.length && password.length < PASSWORD_MIN ? ' over' : ''}`}>
                    {password.length} / {PASSWORD_MIN}+ 
                  </span>
                )}
              </label>
              <div className="pw-row">
                <input
                  id="auth-pw"
                  className="input"
                  type={showPw ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                  placeholder={mode === 'login' ? '输入登录口令' : `至少 ${PASSWORD_MIN} 位，不能包含用户名`}
                />
                <button
                  type="button"
                  className="icon-btn pw-toggle"
                  onClick={() => setShowPw((v) => !v)}
                  title={showPw ? '隐藏口令' : '显示口令'}
                  aria-label={showPw ? '隐藏口令' : '显示口令'}
                  aria-pressed={showPw}
                >
                  <Icon name={showPw ? 'x' : 'eye'} size={15} />
                </button>
              </div>
            </div>

            {needInvite && (
              <div className="field">
                <label className="form-label" htmlFor="auth-invite">
                  邀请码<span className="req">*</span>
                </label>
                <input
                  id="auth-invite"
                  className="input mono"
                  value={invite}
                  onChange={(e) => setInvite(e.target.value)}
                  autoComplete="off"
                  placeholder="管理员发放的注册邀请码"
                />
                <span className="hint">
                  <Icon name="shield" size={12} />
                  注册需邀请码，防止批量注册共用同一把模型 Key 刷额度
                </span>
              </div>
            )}

            <div className="notice-slot">
              {err && (
                <div className="notice" role="alert">
                  <Icon name="alert" size={13} />
                  {err}
                </div>
              )}
            </div>

            <button type="submit" className="btn btn-primary btn-lg btn-block" disabled={busy}>
              {busy ? <span className="spinner dark" /> : <Icon name={mode === 'login' ? 'globe' : 'plus'} size={15} />}
              {mode === 'login' ? '登录' : '注册并登录'}
            </button>
          </form>

          <div className="auth-foot">
            <Icon name="info" size={12} />
            <span>
              {mode === 'login' ? (
                <>
                  还没有账号？
                  <button type="button" className="link-btn" onClick={() => switchMode('register')}>
                    注册一个
                  </button>
                </>
              ) : (
                <>
                  已有账号？
                  <button type="button" className="link-btn" onClick={() => switchMode('login')}>
                    去登录
                  </button>
                </>
              )}
            </span>
          </div>
        </div>
      </main>
    </div>
  )
}
