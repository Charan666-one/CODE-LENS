// Where the FastAPI process lives, as seen *from the Next server* — not from
// the browser. That distinction is the whole reason this is a rewrite: the
// browser only ever talks to this origin, so the backend needs no public
// hostname, no CORS grant, and no exposed port. Under compose the value is a
// service name (`http://backend:8000`); on a host it is localhost.


/** @type {import('next').NextConfig} */
const nextConfig = {
  // Produces a self-contained server bundle with only the modules actually
  // imported, so the runtime image needs no node_modules copy.
  output: "standalone",
  // No image optimisation, because there are no images.
  //
  // This is a security decision, not a performance one. Next ships the
  // `/_next/image` endpoint whether or not the app uses `next/image`, and it
  // is backed by `sharp` → `libvips`, which carries four open high-severity
  // CVEs (GHSA-f88m-g3jw-g9cj). The endpoint already refused every URL —
  // remote hosts need `images.remotePatterns`, which is unset, and there is
  // no `public/` directory to serve local files from — so the vulnerable
  // decoder was unreachable. Unreachable is an argument, though, and it is
  // one that breaks quietly the day someone adds a logo. Turning the
  // optimiser off removes the code path instead of reasoning about it.
  images: { unoptimized: true },
  // The frontend renders; the backend computes. Everything under /api is the
  // FastAPI process — one origin for the browser, no CORS dance anywhere.
  async rewrites() {
  return [
    {
      source: "/api/:path*",
      destination: `${process.env.CODELENS_API_URL || "http://127.0.0.1:8000"}/api/:path*`,
    },
  ];
},
  // Security headers. The frontend is the only public origin, so this is the
  // one place they can be set for the whole surface.
  //
  // The CSP is deliberately strict in the directions that matter for this
  // app and honest about the one place it cannot be: `'unsafe-inline'` for
  // styles is required by Next's own injected style tags, and dropping it
  // renders the page unstyled. Scripts get `'unsafe-eval'` for the same
  // structural reason in development only — the production bundle does not
  // need it, and it is omitted there.
  //
  // What the policy actually buys: `connect-src 'self'` means the page
  // cannot exfiltrate to a third-party host, `frame-ancestors 'none'` means
  // it cannot be framed for clickjacking, and `object-src 'none'` removes
  // the plugin surface entirely.
  async headers() {
    const dev = process.env.NODE_ENV !== "production";
    const csp = [
      "default-src 'self'",
      `script-src 'self' 'unsafe-inline'${dev ? " 'unsafe-eval'" : ""}`,
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob:",
      "font-src 'self' data:",
      "connect-src 'self'",
      "worker-src 'self' blob:",
      "object-src 'none'",
      "base-uri 'self'",
      "form-action 'self'",
      "frame-ancestors 'none'",  
    ].join("; ");
    return [
  {
    source: "/:path*",
    headers: [
      { key: "Content-Security-Policy", value: csp },
      { key: "X-Content-Type-Options", value: "nosniff" },
      { key: "X-Frame-Options", value: "DENY" },
      { key: "Referrer-Policy", value: "no-referrer" },
      {
        key: "Permissions-Policy",
        value: "camera=(), microphone=(), geolocation=(), interest-cohort=()",
      },
    ],
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
