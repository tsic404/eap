// SSO end-to-end regression: one-time teardown.
//
// Removes the Redis container THIS run created in global-setup, identified by
// the owner marker (container id) written at creation. A reused or
// caller-provided Redis is never touched: no marker means nothing to remove.
// Removal is best-effort — a missing container (or absent Docker) is not an
// error.

import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, unlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

const REDIS_MARKER = path.join(tmpdir(), "eap-e2e-redis-owner");

export default async function globalTeardown() {
  if (!existsSync(REDIS_MARKER)) {
    return;
  }
  const containerId = readFileSync(REDIS_MARKER, "utf8").trim();
  try {
    execFileSync("docker", ["rm", "-f", containerId], { stdio: "ignore" });
  } catch {
    // Container already gone (or Docker unavailable) — nothing to clean up.
  }
  try {
    unlinkSync(REDIS_MARKER);
  } catch {
    // Marker already removed.
  }
}
