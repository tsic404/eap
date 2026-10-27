# Playwright runner for the browser journeys, for hosts that only have Docker.
#
# Chromium and its shared libraries live in the image, so a machine without
# pnpm, a glibc browser or the backend venv can still drive the compose stack.
# `scripts/e2e.sh` builds this image and runs it against the already-started
# QA stack; see README «容器内 e2e 运行器».
FROM node:22-bookworm-slim

# Supplied by `scripts/e2e.sh` from pnpm-lock.yaml. There is deliberately no
# default: the runner must exercise the same Playwright build as the repo's
# tests, so a caller that cannot name the version fails instead of silently
# running an older one.
ARG PLAYWRIGHT_VERSION

ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /opt/e2e
COPY package.json package-lock.json ./

# The committed package.json names the expected version; the caller's build arg
# must agree. `npm ci` then installs exactly the committed lockfile graph, so
# transitive dependencies do not float between builds. `--with-deps` installs
# Chromium's runtime libraries through apt, so the browser is executable inside
# the image rather than depending on the host's loader or FHS layout.
RUN test -n "${PLAYWRIGHT_VERSION}" \
    && node -e "const p = require('./package.json'); const pinned = p.devDependencies['@playwright/test']; if (pinned !== process.argv[1]) { console.error('e2e runner pins ' + pinned + ', repo resolves ' + process.argv[1]); process.exit(1); }" "${PLAYWRIGHT_VERSION}" \
    && npm ci --no-audit --no-fund \
    && npx playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/*

COPY entrypoint.sh /usr/local/bin/e2e-entrypoint.sh
RUN chmod +x /usr/local/bin/e2e-entrypoint.sh

ENTRYPOINT ["/usr/local/bin/e2e-entrypoint.sh"]
CMD ["/opt/e2e/node_modules/.bin/playwright", "--version"]
