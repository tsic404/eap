#!/bin/sh
# Materialise a writable copy of the mounted frontend tree and lend it the
# runner's node_modules. The checkout is bind-mounted read-only at /src, so
# Playwright never writes into the caller's working tree; reports and test
# results go to /out, which `scripts/e2e.sh` mounts from the host.
set -eu

rm -rf /work
mkdir -p /work
# A host checkout may carry node_modules/.next; neither belongs in the runner,
# and the host's pnpm symlinks would shadow the image's own packages.
cd /src
find . -mindepth 1 -maxdepth 1 ! -name node_modules ! -name .next -exec cp -a {} /work/ \;
ln -sfn /opt/e2e/node_modules /work/node_modules

cd /work
exec "$@"
