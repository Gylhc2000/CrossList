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
  descMax: number
  mainImage: string
}

export interface PlatformMeta {
  key: string
  name: string
  imageSize: string
  defaultMarket: string
  fileExt: string
  rules: PlatformRules
}

export interface StepMeta {
  key: string
  title: string
  desc: string
}

export interface MetaResponse {
  baseUrl: string
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
}

export interface JobReport {
  generated_at?: string
  models?: { text?: string; image?: string | null }
  platform_count?: number
  language_scores?: { platform: string; language: string; score: number }[]
  error_count?: number
  warn_count?: number
  image_ok?: number
  image_total?: number
  image_masked?: number
  image_failed?: number
  retry_rounds?: Record<string, number>
  plan_source?: string
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
