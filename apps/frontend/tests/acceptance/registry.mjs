/**
 * Frontend acceptance-case registry: design identifiers and their executable
 * modules, mirroring `apps/backend/tests/acceptance/registry.py`. The design
 * repository's `docs/03-acceptance.md` fixes each identifier; this repo carries
 * one module per identifier under `tests/acceptance/`.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";

export const ACCEPTANCE_DIR = path.dirname(fileURLToPath(import.meta.url));

// The recall-test journey (AC-20) is a browser-side case and rides this suite
// alongside the technical cases, mirroring the backend registry's practice.
export const TECH_TC_IDS = ["TC-tech-ui-01", "TC-kb-06"];

export function modulePath(tcId) {
  return path.join(
    ACCEPTANCE_DIR,
    `test_${tcId.toLowerCase().replaceAll("-", "_")}.test.tsx`,
  );
}
