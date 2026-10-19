/**
 * Registry integrity for the frontend acceptance suite: each design identifier
 * maps to exactly one module, the module declares the same identifier, and the
 * by-identifier entry lists and validates identifiers.
 */
import { execFileSync, spawnSync } from "node:child_process";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { ACCEPTANCE_DIR, TECH_TC_IDS, modulePath } from "./registry.mjs";

const MODULE_PATTERN = /^test_tc_tech_[a-z0-9_]+\.test\.tsx$/;
const RUNNER = path.join(ACCEPTANCE_DIR, "run.mjs");

describe("frontend acceptance registry", () => {
  it("maps every identifier to a module that declares it", () => {
    for (const tcId of TECH_TC_IDS) {
      const file = modulePath(tcId);
      expect(readFileSync(file, "utf8"), file).toContain(
        `export const TC_ID = "${tcId}";`,
      );
    }
  });

  it("keeps the module directory and the identifier list one-to-one", () => {
    const modules = readdirSync(ACCEPTANCE_DIR)
      .filter((name) => MODULE_PATTERN.test(name))
      .sort();
    const registered = TECH_TC_IDS.map((tcId) =>
      path.basename(modulePath(tcId)),
    ).sort();
    expect(modules).toEqual(registered);
  });

  it("lists identifiers through the by-identifier entry", () => {
    const output = execFileSync(process.execPath, [RUNNER, "--list"], {
      encoding: "utf8",
    });
    expect(output.trim().split("\n")).toEqual([...TECH_TC_IDS]);
  });

  it("rejects an identifier without a module", () => {
    const result = spawnSync(process.execPath, [RUNNER, "TC-tech-unknown"], {
      encoding: "utf8",
    });
    expect(result.status).toBe(2);
    expect(result.stderr).toContain("TC-tech-unknown");
  });
});
