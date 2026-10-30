/**
 * Frontend acceptance-case registry: design identifiers and their executable
 * modules, mirroring `apps/backend/tests/acceptance/registry.py`. The design
 * repository's `docs/03-acceptance.md` fixes each identifier; this repo carries
 * one module per identifier under `tests/acceptance/`.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ACCEPTANCE_DIR = path.dirname(fileURLToPath(import.meta.url));

// Frontend acceptance cases ride this suite alongside the browser-side
// journeys, mirroring the backend registry's practice.
export const TECH_TC_IDS = [
  "TC-tech-ui-01",
  "TC-kb-01",
  "TC-kb-06",
  "TC-sec-02",
  "TC-tool-05",
  "TC-auth-02",
  "TC-agent-01",
  "TC-agent-04",
  "TC-agent-06",
];

export function modulePath(tcId) {
  return path.join(
    ACCEPTANCE_DIR,
    `test_${tcId.toLowerCase().replaceAll("-", "_")}.test.tsx`,
  );
}
