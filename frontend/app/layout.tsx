import type { Metadata, Viewport } from 'next'
import './globals.css'

export const metadata: Metadata = {
  title: '跨境智上 Agent · CrossList AI',
  description:
    '一键多平台商品上架素材智能体 —— 从商品原始信息到 Amazon / AliExpress / Shopee / TikTok Shop 批量上传素材包的全链路自动化',
  keywords: ['跨境电商', '多平台铺货', 'AI Listing', 'Amazon Flat File', '批量上传'],
}

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  themeColor: '#ff6b2c',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  )
}
