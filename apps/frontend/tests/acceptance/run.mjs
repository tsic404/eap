#!/usr/bin/env node
/**
 * Run frontend acceptance cases by their design identifier.
 *
 * Run from `apps/frontend` (or through `pnpm acceptance` / `make acceptance-ui`):
 *
 *   node tests/acceptance/run.mjs                  # every frontend acceptance case
 *   node tests/acceptance/run.mjs TC-tech-ui-01    # one case, by identifier
 *   node tests/acceptance/run.mjs --list           # identifiers available here
 */
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { TECH_TC_IDS, modulePath } from "./registry.mjs";

const args = process.argv.slice(2);

if (args.includes("--list")) {
  console.log(TECH_TC_IDS.join("\n"));
  process.exit(0);
}

const unknown = args.filter((tcId) => !TECH_TC_IDS.includes(tcId));
if (unknown.length > 0) {
  console.error(`unknown acceptance case(s): ${unknown.join(", ")}`);
  process.exit(2);
}

const frontendDir = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  "../..",
);
const vitest = path.join(frontendDir, "node_modules", ".bin", "vitest");
const targets = (args.length > 0 ? args : TECH_TC_IDS).map(modulePath);
const result = spawnSync(vitest, ["run", ...targets], {
  cwd: frontendDir,
  stdio: "inherit",
});
process.exit(result.status ?? 1);
