/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // 前端 3000 → 后端 8000，同源转发，避免跨域与 SSE 预检问题
  async rewrites() {
    return [
      {
        source: '/api/:path*',
        destination: 'http://localhost:8000/api/:path*',
      },
    ]
  },
}

module.exports = nextConfig
