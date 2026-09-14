/** @type {import('next').NextConfig} */
// 后端地址（去掉结尾斜杠，避免拼出 //api）
//   同机部署（默认）        → http://localhost:8000
//   后端在另一台机器/容器    → 构建前设置 BACKEND_ORIGIN=http://<服务器IP>:8000
// ⚠️ rewrites 在 `next build` 期就被固化进 .next/routes-manifest.json，
//    所以改了 BACKEND_ORIGIN 必须【重新 build】，只重启进程不生效。
const BACKEND_ORIGIN = (process.env.BACKEND_ORIGIN || 'http://localhost:8000').replace(/\/+$/, '')

const nextConfig = {
  reactStrictMode: true,
  // 浏览器只访问同源的 /api/*，由 Next 服务端转发给后端：
  // 前端侧永远同源，规避跨域与 SSE 预检问题（api.ts 里全是相对路径）。
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: `${BACKEND_ORIGIN}/api/:path*`,
      },
    ]
  },
}

module.exports = nextConfig
