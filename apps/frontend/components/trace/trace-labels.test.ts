import { describe, expect, it } from "vitest";

import {
  runLogStatusLabel,
  runLogStatusVariant,
} from "./trace-labels";

describe("runLogStatusVariant", () => {
  it("maps the four statuses to badge variants", () => {
    expect(runLogStatusVariant("success")).toBe("success");
    expect(runLogStatusVariant("failed")).toBe("danger");
    expect(runLogStatusVariant("running")).toBe("info");
    expect(runLogStatusVariant("blocked")).toBe("warning");
  });

  it("defaults unknown and absent statuses to the default variant", () => {
    expect(runLogStatusVariant("exploded")).toBe("default");
    expect(runLogStatusVariant(null)).toBe("default");
    expect(runLogStatusVariant(undefined)).toBe("default");
  });
});

describe("runLogStatusLabel", () => {
  it("returns Chinese labels for known statuses", () => {
    expect(runLogStatusLabel("success")).toBe("成功");
    expect(runLogStatusLabel("failed")).toBe("失败");
  });

  it("falls back to the raw value for unknown statuses", () => {
    expect(runLogStatusLabel("queued")).toBe("queued");
    expect(runLogStatusLabel(null)).toBe("未知");
  });
});
