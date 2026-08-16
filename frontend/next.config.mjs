// Where the FastAPI process lives, as seen *from the Next server* — not from
// the browser. That distinction is the whole reason this is a rewrite: the
// browser only ever talks to this origin, so the backend needs no public
// hostname, no CORS grant, and no exposed port. Under compose the value is a
// service name (`http://backend:8000`); on a host it is localhost.
const API_ORIGIN = process.env.CODELENS_API_URL ?? "http://127.0.0.1:8000";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Produces a self-contained server bundle with only the modules actually
  // imported, so the runtime image needs no node_modules copy.
  output: "standalone",
  // The frontend renders; the backend computes. Everything under /api is the
  // FastAPI process — one origin for the browser, no CORS dance anywhere.
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${API_ORIGIN}/api/:path*`,
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
