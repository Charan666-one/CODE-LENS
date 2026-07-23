/** @type {import('next').NextConfig} */
const nextConfig = {
  // The frontend renders; the backend computes. Everything under /api is the
  // FastAPI process — one origin for the browser, no CORS dance in dev.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: "http://127.0.0.1:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
