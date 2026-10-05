'use client'

import { Icon, brandOf } from '@/components/ui'
import { downloadUrl, fmtSize } from '@/lib/api'
import type { JobSnapshot, PlatformFiles } from '@/lib/types'

interface Props {
  job: JobSnapshot
  files: PlatformFiles[]
  filesState: 'idle' | 'loading' | 'ready' | 'error'
  onReload: () => void
  onBackPreview: () => void
  onRestart: () => void
  onToast: (msg: string, kind?: 'info' | 'ok' | 'err') => void
}

const typeIcon = (type: string) =>
  type === 'images' ? ('image' as const) : type === 'report' ? ('fileText' as const) : type === 'archive' ? ('package' as const) : type === 'template' || type === 'worksheet' ? ('sheet' as const) : ('file' as const)

export default function DownloadView({
  job,
  files,
  filesState,
  onReload,
  onBackPreview,
  onRestart,
  onToast,
}: Props) {
  const rep = job.result.report
  const platforms = job.result.platforms ?? []
  const errCount = rep?.error_count ?? 0
  const warnCount = rep?.warn_count ?? 0
  const retryRounds = Object.values(rep?.retry_rounds ?? {})
  const scores = rep?.language_scores ?? []
  const avgScore = scores.length
    ? Math.round(scores.reduce((a, b) => a + b.score, 0) / scores.length)
    : 0
  const imageOk = rep?.image_ok ?? 0
  const imageTotal = rep?.image_total ?? 0
  const imageMasked = rep?.image_masked ?? 0
  const imageFailed = rep?.image_failed ?? 0
  const manualTotal = rep?.manual_field_count ?? 0

  const totalSize = files.reduce(
    (sum, p) => sum + (p.files ?? []).reduce((s, f) => s + (f.size ?? 0), 0),
    0,
  )
  // JobManager 为内存态：后端重启后旧任务的所有接口都会 404，产物不可恢复
  const stale = filesState === 'ready' && platforms.length > 0 && totalSize === 0 && !files.some((f) => (f.files ?? []).length > 0)

  const copyManifest = async () => {
    const lines = platforms.map((p) => {
      const list = files.find((f) => f.key === (p.pk ?? p.key))?.files ?? []
      return `${p.name}（${p.market_label} · ${p.language}）\n  ${list.map((f) => `${f.name}  ${fmtSize(f.size)}`).join('\n  ') || '（无文件）'}`
    })
    const text = [
      `CrossList AI 上架素材清单`,
      `商品：${job.result.knowledge_card?.product_name_zh ?? '—'}`,
      `任务 ID：${job.id}`,
      '',
      ...lines,
    ].join('\n')
    try {
      await navigator.clipboard.writeText(text)
      onToast('素材清单已复制到剪贴板', 'ok')
    } catch {
      onToast('复制失败，请手动选择文本', 'err')
    }
  }

  return (
    <section>
      <div className="page-head">
        <div>
          <h1 className="page-title">
            上架素材包下载
            <span className="badge badge-ok">
              <Icon name="checkCircle" size={12} />
              {errCount === 0 ? '合规校验通过' : `${errCount} 项待修正`}
            </span>
          </h1>
          <p className="page-sub">
            {job.result.knowledge_card?.product_name_zh ?? '商品'} · 共 {platforms.length}
            份站点素材包：文案可直接取用，图片已按平台尺寸适配。批量模板的列集合是整理出来的演示版，
            {manualTotal
              ? `正式上传前请按各平台《上架对照表》补齐 ${manualTotal} 项只有卖家/平台才有的字段。`
              : '上传前请按各平台《上架对照表》逐项核对一遍。'}
          </p>
        </div>
        <div className="page-head-aside">
          <button className="btn btn-ghost btn-sm" onClick={onBackPreview}>
            <Icon name="chevronLeft" size={14} />
            结果预览
          </button>
          <button className="btn btn-ghost btn-sm" onClick={onRestart}>
            <Icon name="refresh" size={14} />
            新建任务
          </button>
        </div>
      </div>

      {/* 指标 */}
      <div className="metric-grid" style={{ marginBottom: 18 }}>
        <div className="metric">
          <div className="metric-l">
            <Icon name="package" size={12} />
            平台素材包
          </div>
          <div className="metric-v">
            {platforms.length}
            <small>套</small>
          </div>
        </div>
        <div className={`metric ${errCount ? 'bad' : 'good'}`}>
          <div className="metric-l">
            <Icon name="shield" size={12} />
            不合规项
          </div>
          <div className="metric-v">
            {errCount}
            <small>项</small>
          </div>
        </div>
        <div className="metric">
          <div className="metric-l">
            <Icon name="alert" size={12} />
            警告项
          </div>
          <div className="metric-v">
            {warnCount}
            <small>项</small>
          </div>
        </div>
        <div className={`metric ${avgScore >= 85 ? 'good' : ''}`}>
          <div className="metric-l">
            <Icon name="globe" size={12} />
            平均语言分
          </div>
          <div className="metric-v">
            {avgScore || '—'}
            <small>/100</small>
          </div>
        </div>
        <div className="metric">
          <div className="metric-l">
            <Icon name="image" size={12} />
            素材图
          </div>
          <div className="metric-v">
            {imageTotal ? `${imageOk}/${imageTotal}` : '—'}
            <small>张</small>
          </div>
        </div>
        <div className="metric">
          <div className="metric-l">
            <Icon name="fileText" size={12} />
            上传前待补
          </div>
          <div className="metric-v">
            {manualTotal}
            <small>项</small>
          </div>
        </div>
        <div className="metric">
          <div className="metric-l">
            <Icon name="refresh" size={12} />
            自动修正
          </div>
          <div className="metric-v">
            {retryRounds.length ? Math.max(...retryRounds) : 0}
            <small>轮</small>
          </div>
        </div>
      </div>

      {/* 平台文件卡 */}
      <div className="stack" style={{ gap: 12 }}>
        {platforms.map((p) => {
          const list = files.find((f) => f.key === (p.pk ?? p.key))?.files ?? []
          const b = brandOf(p.pk ?? p.key)
          const size = list.reduce((s, f) => s + (f.size ?? 0), 0)
          const manualN = p.manual_fields?.length ?? 0
          return (
            <div className="dl-card" key={p.key}>
              <span className="dl-logo" style={{ background: b.bg }}>
                {b.mark}
              </span>
              <div className="dl-info">
                <div className="dl-name">
                  <span className="tp-flag">{p.flag}</span>
                  {p.name}
                  <span className="badge badge-neutral">{p.language}</span>
                  {Number(p.quality?.score ?? 0) > 0 && !p.quality?.failed && (
                    <span className="badge badge-ok">{p.quality?.score} 分</span>
                  )}
                </div>
                <div className="dl-sub">
                  {p.market_label} · 主图 {p.image_size} · {list.length || '—'} 个文件
                  {size ? ` · ${fmtSize(size)}` : ''}
                  {manualN ? ` · ${manualN} 项待补` : ''}
                </div>
                <div className="dl-files">
                  {list.length ? (
                    list.map((f) => (
                      <span className="file-chip" key={f.name} title={f.name}>
                        <Icon name={typeIcon(f.type)} size={12} />
                        {f.name}
                        <span className="sz">{fmtSize(f.size)}</span>
                      </span>
                    ))
                  ) : filesState === 'error' ? (
                    /* 请求失败 ≠ 产物已失效：说成后者会骗人重跑一次任务 */
                    <span className="t-sm" style={{ color: 'var(--err-500)' }}>
                      产物清单加载失败（服务未响应）·{' '}
                      <a
                        href="#"
                        onClick={(e) => {
                          e.preventDefault()
                          onReload()
                        }}
                      >
                        重试
                      </a>
                    </span>
                  ) : filesState === 'ready' ? (
                    <span className="t-sm" style={{ color: 'var(--err-500)' }}>
                      产物不可用
                    </span>
                  ) : (
                    <>
                      <span className="sk" style={{ height: 22, width: 120 }} />
                      <span className="sk" style={{ height: 22, width: 96 }} />
                    </>
                  )}
                </div>
              </div>
              {stale ? (
                <button className="btn btn-ghost btn-sm" disabled title="任务产物已失效">
                  <Icon name="download" size={14} />
                  下载
                </button>
              ) : (
                <a className="btn btn-ghost btn-sm" href={downloadUrl(job.id, p.pk ?? p.key)}>
                  <Icon name="download" size={14} />
                  下载
                </a>
              )}
            </div>
          )
        })}
      </div>

      {imageMasked + imageFailed > 0 && (
        <div className="notice multi" style={{ marginTop: 14 }}>
          <Icon name="shield" size={15} />
          <div>
            {imageMasked > 0 && (
              <>
                本包 <b>{imageMasked} 张图为脱敏生成</b>：出图时被图像模型 IP/版权风控拦截，
                已自动移除品牌名、角色名等 IP 特征词后重生成，画面只保留品类/颜色/材质等通用特征，
                <b>不是原 IP 本体形象</b>，正式上架请替换为自有实拍图或已授权素材。
              </>
            )}
            {imageMasked > 0 && imageFailed > 0 && <br />}
            {imageFailed > 0 && (
              <>
                另有 <b>{imageFailed} 张降级为占位图</b>，需人工补图。
              </>
            )}
            <br />
            回到预览页可逐张查看每张图的处理方式。
          </div>
        </div>
      )}

      {stale && (
        <div className="notice" style={{ marginTop: 14 }}>
          <Icon name="alert" size={15} />
          <span>
            该任务的产物已不可下载。任务数据保存在后端内存中，<b>后端重启或服务回收后会清空</b>
            ，历史记录里的旧任务将全部失效。请回到输入页重新运行一次生成任务。
            <a
              href="#"
              style={{ marginLeft: 8 }}
              onClick={(e) => {
                e.preventDefault()
                onReload()
              }}
            >
              重试加载 →
            </a>
          </span>
        </div>
      )}

      {/* 一键下载 */}
      <div className="dl-cta">
        <div style={{ flex: 1, minWidth: 220 }}>
          <div className="strong t-lg">一键下载全部素材包</div>
          <div className="t-sm" style={{ color: 'var(--ink-400)', marginTop: 3 }}>
              {platforms.length} 份站点素材 + 校验报告，打包为单个 ZIP（
              {stale ? '产物已失效' : totalSize ? fmtSize(totalSize) : '计算中'}）
            </div>
          </div>
          <div className="row">
            <button
              className="btn btn-ghost btn-sm"
              onClick={copyManifest}
            >
              <Icon name="copy" size={14} />
              复制清单
            </button>
            {stale ? (
              <button className="btn btn-primary btn-lg" disabled title="任务产物已失效">
                <Icon name="download" size={16} />
                下载全部 ZIP
              </button>
            ) : (
              <a className="btn btn-primary btn-lg" href={downloadUrl(job.id, 'all')}>
                <Icon name="download" size={16} />
                下载全部 ZIP
              </a>
            )}
          </div>
      </div>

      {/* 运行详情 */}
      <div className="section-card" style={{ marginTop: 18 }}>
        <div className="section-head">
          <span className="section-idx">
            <Icon name="activity" size={13} />
          </span>
          <span className="section-title">运行详情</span>
          <span className="section-desc">任务 ID {job.id}</span>
        </div>
        <div className="section-body">
          <div className="kv-grid">
            <div className="kv">
              <span className="kv-k">
                <Icon name="cpu" size={12} />
                使用模型
              </span>
              <span className="kv-v">
                {rep?.models?.text ?? '—'}
                {rep?.models?.image ? ` + ${rep.models.image}` : ''}
              </span>
            </div>
            <div className="kv">
              <span className="kv-k">
                <Icon name="clock" size={12} />
                生成时间
              </span>
              <span className="kv-v">
                {rep?.generated_at ? new Date(rep.generated_at).toLocaleString('zh-CN') : '—'}
              </span>
            </div>
            <div className="kv">
              <span className="kv-k">
                <Icon name="layers" size={12} />
                计划来源
              </span>
              <span className="kv-v">{rep?.plan_source === 'llm' ? 'LLM 规划' : '规则兜底'}</span>
            </div>
            <div className="kv">
              <span className="kv-k">
                <Icon name="refresh" size={12} />
                修正轮次
              </span>
              <span className="kv-v">
                {retryRounds.length ? retryRounds.map((n) => `${n} 轮`).join(' / ') : '无需修正'}
              </span>
            </div>
          </div>

          {scores.length > 0 && (
            <>
              <div className="divider" />
              <div className="panel-title" style={{ marginBottom: 8 }}>
                <Icon name="globe" size={12} />
                各平台语言质量
              </div>
              <div className="kv-grid">
                {scores.map((s, i) => (
                  <div className="kv" key={i}>
                    <span className="kv-k">
                      {s.platform} · {s.language}
                    </span>
                    <span className="kv-v">
                      <span className={`badge ${s.score >= 85 ? 'badge-ok' : s.score >= 70 ? 'badge-warn' : 'badge-err'}`}>
                        {s.failed ? '—' : `${s.score} 分`}
                      </span>
                    </span>
                  </div>
                ))}
              </div>
            </>
          )}

          {job.warnings.length > 0 && (
            <div className="notice" style={{ marginTop: 14 }}>
              <Icon name="alert" size={14} />
              <span>{job.warnings.join('；')}</span>
            </div>
          )}
        </div>
      </div>
    </section>
  )
}
