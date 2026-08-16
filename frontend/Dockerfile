# CodeLens frontend — Next.js, built once and served.
#
# Three stages so the runtime image carries neither the toolchain nor the full
# node_modules tree. `output: "standalone"` (next.config.mjs) emits a server
# bundle containing only the modules actually imported, which is what makes
# the last stage a copy of three directories instead of an npm install.

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
# `npm ci` and not `npm install`: the lockfile is the build input, and a
# deploy that silently resolves a different tree than the one tested is not a
# deploy of what was tested.
RUN npm ci


FROM node:22-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Where the API lives, as seen from the Next server. It has to be a build arg
# rather than a runtime variable: Next resolves rewrite destinations when the
# config loads during the build, so setting it on the running container would
# be read too late and silently keep the localhost default.
ARG CODELENS_API_URL=http://127.0.0.1:8000
ENV CODELENS_API_URL=${CODELENS_API_URL} \
    NEXT_TELEMETRY_DISABLED=1
RUN npm run build


FROM node:22-slim AS runtime
WORKDIR /app

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    # The standalone server binds to $HOSTNAME, and Docker sets HOSTNAME to
    # the container id. It therefore listened only on the container's own IP:
    # published ports still worked, so from the host everything looked fine,
    # while nothing was listening on loopback and the healthcheck below could
    # never pass. An container stuck "unhealthy" is one `depends_on:
    # service_healthy` away from blocking someone else's stack.
    HOSTNAME=0.0.0.0

# `public` is copied conditionally-shaped: the standalone server expects the
# static assets beside it, and a missing .next/static is a page that renders
# unstyled rather than an error anyone would notice in CI.
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static

USER node
EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD node -e "require('http').get('http://127.0.0.1:3000/',r=>process.exit(r.statusCode===200?0:1)).on('error',()=>process.exit(1))"

# The standalone build's own entrypoint. Not `npm start`, which would need the
# Next CLI and the node_modules this image deliberately does not have.
CMD ["node", "server.js"]
