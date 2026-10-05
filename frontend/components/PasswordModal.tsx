'use client'

import { useEffect, useState } from 'react'
import { ApiError, api } from '@/lib/api'
import { Icon, Modal } from '@/components/ui'

interface Props {
  open: boolean
  username: string
  onClose: () => void
  onDone: (msg: string) => void
  onToast: (msg: string, kind?: 'info' | 'ok' | 'err') => void
}

/**
 * 自助改口令。没有邮件通道，所以不做"忘记口令"——
 * 但改口令必须自助可用，否则口令泄露后没有止损手段。
 */
export default function PasswordModal({ open, username, onClose, onDone, onToast }: Props) {
  const [cur, setCur] = useState('')
  const [next, setNext] = useState('')
  const [again, setAgain] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  useEffect(() => {
    if (open) {
      setCur('')
      setNext('')
      setAgain('')
      setErr('')
    }
  }, [open])

  const mismatch = !!again && again !== next
  const tooShort = !!next && next.length < 8

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (busy) return
    if (!cur || !next) {
      setErr('请填写当前口令与新口令')
      return
    }
    if (next.length < 8) {
      setErr('新口令至少 8 位')
      return
    }
    if (next !== again) {
      setErr('两次输入的新口令不一致')
      return
    }
    setBusy(true)
    setErr('')
    try {
      const r = await api.changePassword({ currentPassword: cur, newPassword: next })
      onDone('口令已更新')
      onClose()
      onToast(
        r.revokedSessions > 0
          ? `口令已更新，已同时退出其它 ${r.revokedSessions} 个登录会话`
          : '口令已更新',
        'ok',
      )
    } catch (ex) {
      setErr((ex as ApiError)?.message || '修改失败')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Modal
      open={open}
      title="修改口令"
      subtitle={`账号：${username} · 改成功后其它设备上的登录会话会被退出`}
      icon="shield"
      width={420}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={onClose}>
            取消
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={busy}>
            {busy && <span className="spinner dark" />}
            确认修改
          </button>
        </>
      }
    >
      <form className="field" style={{ display: 'flex', flexDirection: 'column', gap: 13 }} onSubmit={submit} noValidate>
        <div className="field">
          <label className="form-label" htmlFor="pw-cur">
            当前口令<span className="req">*</span>
          </label>
          <input
            id="pw-cur"
            className="input"
            type="password"
            value={cur}
            onChange={(e) => setCur(e.target.value)}
            autoComplete="current-password"
          />
        </div>
        <div className="field">
          <label className="form-label" htmlFor="pw-new">
            新口令<span className="req">*</span>
            <span className={`counter${tooShort ? ' over' : ''}`}>{next.length} / 8+</span>
          </label>
          <input
            id="pw-new"
            className="input"
            type="password"
            value={next}
            onChange={(e) => setNext(e.target.value)}
            autoComplete="new-password"
            placeholder="至少 8 位，不能包含用户名"
          />
        </div>
        <div className="field">
          <label className="form-label" htmlFor="pw-again">
            确认新口令<span className="req">*</span>
          </label>
          <input
            id="pw-again"
            className={`input${mismatch ? ' over' : ''}`}
            type="password"
            value={again}
            onChange={(e) => setAgain(e.target.value)}
            autoComplete="new-password"
            aria-invalid={mismatch || undefined}
          />
          {mismatch && (
            <span className="hint warn">
              <Icon name="alert" size={12} />
              两次输入不一致
            </span>
          )}
        </div>

        <div className="notice-slot">
          {err && (
            <div className="notice" role="alert">
              <Icon name="alert" size={13} />
              {err}
            </div>
          )}
        </div>
      </form>
    </Modal>
  )
}
