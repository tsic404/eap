FROM node:22-alpine AS base
RUN corepack enable
WORKDIR /repo

FROM base AS deps
# The lockfile carries prebuilt binaries for every platform; resolve only the
# image's own, leaving unmapped platforms to pnpm's own detection.
ARG TARGETPLATFORM
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/frontend/package.json ./apps/frontend/
COPY packages/api-contract/package.json ./packages/api-contract/
# Mounted at pnpm's default store path so the store stays out of the image layer
# and later stages still agree with the recorded install state; a rebuild that
# misses this layer then refetches only what changed. --libc musl matches the
# alpine base, and --trust-lockfile drops the per-install re-verification of all
# 777 lockfile entries (~2 min here) that CI already runs on the same commit.
RUN --mount=type=cache,id=pnpm-store,target=/root/.local/share/pnpm/store,sharing=locked \
    platform='' \
    && case "${TARGETPLATFORM}" in \
         linux/amd64) platform='--os linux --cpu x64 --libc musl' ;; \
         linux/arm64) platform='--os linux --cpu arm64 --libc musl' ;; \
       esac \
    && pnpm install --frozen-lockfile \
        --fetch-retries 5 \
        --trust-lockfile \
        ${platform}

FROM base AS build
COPY . .
COPY --from=deps /repo/node_modules ./node_modules
COPY --from=deps /repo/apps/frontend/node_modules ./apps/frontend/node_modules
COPY --from=deps /repo/packages/api-contract/node_modules ./packages/api-contract/node_modules
# `next build` would repeat the ESLint pass that CI already runs as its own
# gate; skipping it here keeps this container build off the memory ceiling.
ENV NEXT_SKIP_ESLINT_IN_BUILD=1
# pnpm re-checks the install before running the script; the same store mount keeps
# that check off the network.
RUN --mount=type=cache,id=pnpm-store,target=/root/.local/share/pnpm/store,sharing=locked \
    pnpm --filter @eap/frontend build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
COPY --from=build /repo/apps/frontend/.next/standalone ./
COPY --from=build /repo/apps/frontend/.next/static ./apps/frontend/.next/static
COPY --from=build /repo/apps/frontend/public ./apps/frontend/public

EXPOSE 3000
HEALTHCHECK --interval=10s --timeout=3s --start-period=15s --retries=5 \
    CMD wget -qO- http://localhost:3000/ > /dev/null || exit 1

CMD ["node", "apps/frontend/server.js"]
