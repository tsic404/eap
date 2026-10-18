/**
 * Locates the frontend package root for tests that read repository files.
 * Vitest runs with the package as cwd; a repo-root invocation is tolerated so
 * the module also works from a workspace-wide runner.
 */
import { existsSync } from "node:fs";
import path from "node:path";

const CANDIDATES = [process.cwd(), path.join(process.cwd(), "apps/frontend")];
const root = CANDIDATES.find((candidate) =>
  existsSync(path.join(candidate, "app/globals.css")),
);
if (root === undefined) {
  throw new Error(
    `cannot locate apps/frontend from ${process.cwd()} (app/globals.css missing)`,
  );
}

export const FRONTEND_ROOT = root;
