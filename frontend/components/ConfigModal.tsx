'use client'

import { useEffect, useState } from 'react'
import { Icon, Modal } from '@/components/ui'
import { api } from '@/lib/api'
import type { AppConfig, MetaResponse } from '@/lib/types'

interface Props {
  open: boolean
  meta: MetaResponse | null
  config: AppConfig | null
  onClose: () => void
  onToast: (msg: string, kind?: 'info' | 'ok' | 'err') => void
  onSaved: (patch: Partial<AppConfig>) => void
}

export default function ConfigModal({ open, meta, config, onClose, onToast, onSaved }: Props) {
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')   // 仅在用户主动输入新 Key 时提交，明文不回显
  const [textModel, setTextModel] = useState('')
  const [imageModel, setImageModel] = useState('')
  const [testing, setTesting] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testMsg, setTestMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    if (!open || !config) return
    setBaseUrl(config.baseUrl)
    setApiKey('')   // 后端已脱敏，明文不回显；留空 = 不修改现有 Key
    setTextModel(config.textModel)
    setImageModel(config.imageModel)
    setTestMsg(null)
  }, [open, config])

  const handleTest = async () => {
    setTesting(true)
    setTestMsg(null)
    try {
      const r = await api.testConnection({ baseUrl, apiKey: apiKey || undefined, textModel: textModel || undefined })
      setTestMsg(
        r.ok
          ? { ok: true, text: `连接成功 · 模型 ${r.model} · 耗时 ${r.latencyMs}ms` }
          : { ok: false, text: `连接失败：${r.error}` },
      )
    } catch (e) {
      setTestMsg({ ok: false, text: `连接失败：${(e as Error).message}` })
    } finally {
      setTesting(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      const patch: Partial<AppConfig> = { baseUrl, textModel, imageModel }
      if (apiKey.trim()) patch.apiKey = apiKey.trim()   // 留空 = 保留现有 Key
      const r = await api.saveConfig(patch)
      onSaved({ ...patch, hasKey: r.hasKey })
      onToast('模型配置已保存', 'ok')
      onClose()
    } catch (e) {
      onToast('保存失败：' + (e as Error).message, 'err')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal
      open={open}
      title="自定义模型服务"
      subtitle="默认使用内置模型服务，无需配置；如需使用自己的 API Key 或模型，可在此替换"
      icon="settings"
      width={640}
      onClose={onClose}
      footer={
        <>
          <button className="btn btn-ghost" onClick={handleTest} disabled={testing}>
            {testing ? <span className="spinner dark" /> : <Icon name="checkCircle" size={14} />}
            {testing ? '测试中…' : '测试连接'}
          </button>
          <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
            {saving ? <span className="spinner" /> : <Icon name="check" size={14} />}
            保存配置
          </button>
        </>
      }
    >
      <div className="field">
        <label className="form-label">
          <span>API Base URL</span>
        </label>
        <input className="input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} />
        <div className="hint">
          <Icon name="info" size={12} />
          对话接口为基地址 + <code>/chat/completions</code>，请求头携带{' '}
          <code>Authorization: Bearer &lt;APIKey&gt;</code>
        </div>
      </div>

      <div className="field" style={{ marginTop: 14 }}>
        <label className="form-label">
          <span>API Key（可选）</span>
          {config?.hasKey && (
            <span className="badge badge-ok">
              已配置 {config.apiKey ? `（${config.apiKey}）` : ''}
            </span>
          )}
        </label>
        <input
          className="input"
          type="password"
          placeholder={config?.hasKey ? '已配置，留空则保持不变；更换请输入新 Key' : '在此填写您的 APIKey'}
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
        />
        <div className="hint">
          <Icon name="shield" size={12} />
          明文仅保存在后端 <code>backend/.env</code>，接口返回时已脱敏，不会上传到任何第三方。
        </div>
      </div>

      <div className="form-grid c2" style={{ marginTop: 14 }}>
        <div className="field">
          <label className="form-label">
            <span>文本推理模型</span>
          </label>
          <select className="select" value={textModel} onChange={(e) => setTextModel(e.target.value)}>
            {(meta?.textModels ?? [textModel]).filter(Boolean).map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
        <div className="field">
          <label className="form-label">
            <span>图像生成模型</span>
          </label>
          <select className="select" value={imageModel} onChange={(e) => setImageModel(e.target.value)}>
            {(meta?.imageModels ?? [imageModel]).filter(Boolean).map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </select>
        </div>
      </div>

      {testMsg && (
        <div
          className={`notice ${testMsg.ok ? 'info' : ''}`}
          style={testMsg.ok ? undefined : { marginTop: 14 }}
        >
          <Icon name={testMsg.ok ? 'checkCircle' : 'alert'} size={14} />
          <span>{testMsg.text}</span>
        </div>
      )}
    </Modal>
  )
}
