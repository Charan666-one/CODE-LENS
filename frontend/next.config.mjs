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
  experimental: {
    // Next's rewrite proxy aborts an unfinished request at 30s
    // (node_modules/next/dist/server/lib/router-utils/proxy-request.js:
    // `proxyTimeout || 30000`) and returns its own bare 500 — no JSON body,
    // so the frontend falls back to displaying the raw status text
    // ("Internal Server Error"). /api/analyze on a large monorepo (e.g.
    // n8n: ~19k parseable files) legitimately takes under a minute; 30s cuts
    // it off mid-clone. 10 minutes covers real repos with headroom. The real
    // fix — an async job so the browser never holds one request open for a
    // whole analysis — is CP-6.2's job queue; this is the honest stopgap
    // until that exists.
    proxyTimeout: 600_000,
  },
};

export default nextConfig;
