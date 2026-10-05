export interface Market {
  key: string
  label: string
  flag: string
  language: string
  langCode: string
}

export interface PlatformRules {
  titleMax: number
  bulletMax: number
  bulletCount: number
  keywordMax: number
  /** 非空表示该平台按 UTF-8 字节而非字符限关键词长度 */
  keywordMaxBytes?: number | null
  descMax: number
  mainImage: string
  /** 规则数值的核对时间；平台会改（Amazon 标题 200→75），无此标记即可能是过期值 */
  asof?: string
}

export interface PlatformMeta {
  key: string
  name: string
  imageSize: string
  defaultMarket: string
  fileExt: string
  /** 该平台真实有站点的市场；不在表内的组合后端会拒 */
  markets?: string[]
  rules: PlatformRules
}

export interface StepMeta {
  key: string
  title: string
  desc: string
}

export interface MetaResponse {
  textModels: string[]
  imageModels: string[]
  audioModels?: string[]
  markets: Market[]
  platforms: PlatformMeta[]
  steps: StepMeta[]
  graph: string
  stack: { backend: string; agent: string; exporters: string }
}

export interface AppConfig {
  baseUrl: string
  apiKey?: string   // 脱敏展示值（sk-****abcd），明文不下发
  textModel: string
  imageModel: string
  audioModel?: string
  hasKey: boolean
  /** 服务端是否允许在运行时改写模型配置（默认关，且仅管理员可用） */
  runtimeConfig?: boolean
}

export interface AuthStatus {
  openSignup: boolean
  inviteRequired: boolean
  hasAccounts: boolean
}

export interface UserMe {
  id: number
  username: string
  isAdmin: boolean
  quota?: { limit: number; used: number; remaining: number }
}

/** 服务端历史条目：跨重启存活，产物是否还在由 hasResult/后端判断 */
export interface HistoryJob {
  jobId: string
  productName: string
  status: 'queued' | 'running' | 'done' | 'error' | 'cancelled'
  error?: string | null
  createdAt: number
  finishedAt?: number | null
  hasResult: boolean
  jobCount?: number
  platformNames?: string[]
  marketLabels?: string[]
  withImages?: boolean
}

export interface StepState {
  key: string
  title: string
  desc: string
  state: 'pending' | 'running' | 'done' | 'error' | 'skipped'
  detail: string
}

export interface LogEntry {
  ts: number
  msg: string
  level: 'info' | 'warn' | 'error'
}

export interface ListingMeta {
  key: string
  label: string
  flag: string
  language: string
  lang_code: string
}

export interface Listing {
  title: string
  bullet_points: string[]
  description: string
  search_terms: string
  meta?: ListingMeta
}

export interface KnowledgeCard {
  product_name_zh?: string
  product_name_en?: string
  category?: string
  brand?: string
  price_source?: 'user' | 'model'
  core_selling_points?: string[]
  specs?: { name: string; value: string }[]
  target_audience?: string[]
  usage_scenarios?: string[]
  visual_features?: Record<string, string>
  image_prompt_subject?: string
  package_contents?: string
  suggested_price?: { currency?: string; value?: number }
  hs_keywords?: string[]
}

export interface CheckItem {
  level: 'error' | 'warn'
  field: string
  msg: string
  fix: string
}

export interface Quality {
  score?: number
  failed?: boolean
  comments?: string
  consistency?: string
  suggestions?: string[]
}

export interface ManualField {
  /** 模板列名 */
  col: string
  /** 为什么我们填不了，以及卖家该去哪拿这个值 */
  why: string
}

export interface PlatformResult {
  key: string          // 任务单元键 "平台:市场"，如 amazon:us
  pk: string           // 平台 key，如 amazon
  name: string
  image_size: string
  image_px: number
  file_ext: string
  flag: string
  market_label: string
  lang_code: string
  language: string
  listing: Listing
  checks: CheckItem[]
  quality: Quality
  rules: PlatformRules
  /** 模板里刻意留空、必须卖家上传前补的列（导出节点回填，校验阶段还没有） */
  manual_fields?: ManualField[]
}

export interface ImageAsset {
  name: string
  label: string
  path: string
  url: string
  ok: boolean
  error?: string
  note?: string
  group: 'main' | 'detail'
  /** 实际送模型的提示词：含 "reference photo" 即说明这张是带实拍参考图生成的 */
  prompt?: string
}

export interface JobReport {
  generated_at?: string
  models?: { text?: string; image?: string | null }
  platform_count?: number
  unit_count?: number
  language_scores?: {
    platform: string
    market?: string
    language: string
    score: number
    failed?: boolean
  }[]
  error_count?: number
  warn_count?: number
  image_ok?: number
  image_total?: number
  image_masked?: number
  image_failed?: number
  retry_rounds?: Record<string, number>
  plan_source?: string
  /** 模板中我们填不了、必须卖家上传前补的列总数 */
  manual_field_count?: number
}

export interface JobResult {
  plan?: Record<string, unknown>
  knowledge_card?: KnowledgeCard
  listings?: Record<string, Listing>
  images?: ImageAsset[]
  platforms?: PlatformResult[]
  artifacts?: Record<string, unknown>
  report?: JobReport
}

export interface JobSnapshot {
  id: string
  status: 'queued' | 'running' | 'done' | 'error' | 'cancelled'
  steps: StepState[]
  logs: LogEntry[]
  warnings: string[]
  result: JobResult
  error: string | null
  started_at: number | null
  finished_at: number | null
  /** 服务重启后由历史记录重建的快照：结果可用，但实时进度已丢，产物可能已过期 */
  cold?: boolean
}

export interface FileItem {
  name: string
  size: number
  type: string
}

export interface PlatformFiles {
  key: string
  name: string
  files: FileItem[]
}

export type SseEvent =
  | { type: 'hello'; job: JobSnapshot }
  | { type: 'start'; steps: StepState[] }
  | { type: 'step'; index: number; state: string; detail: string }
  | { type: 'log'; msg: string; level: string }
  | { type: 'card'; card: KnowledgeCard }
  | { type: 'listing'; market: ListingMeta; listing: Listing }
  | { type: 'listings'; listings: Record<string, Listing> }
  | { type: 'image'; image: ImageAsset }
  | { type: 'platforms'; platforms: PlatformResult[] }
  | { type: 'done'; result: JobResult; started_at?: number | null; finished_at?: number | null }
  | { type: 'fail'; error: string }
  | { type: 'end'; status: string }
