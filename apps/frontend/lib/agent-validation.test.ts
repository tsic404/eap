import { describe, expect, it } from "vitest";

import {
  createAgentSchema,
  MAX_TAGS,
  parseTags,
  type CreateAgentFormValues,
} from "./agent-validation";

const VALID_VALUES: CreateAgentFormValues = {
  agentId: "support-bot",
  name: "客服助手",
  description: "",
  type: "chat",
  category: "",
  icon: "🤖",
  tags: "",
  modelProvider: "",
  modelId: "",
  modelName: "",
  prompt: "",
  toolIds: [],
};

describe("createAgentSchema", () => {
  it("accepts valid values", () => {
    expect(createAgentSchema.safeParse(VALID_VALUES).success).toBe(true);
  });

  it("rejects a whitespace-only name", () => {
    const result = createAgentSchema.safeParse({ ...VALID_VALUES, name: "   " });
    expect(result.success).toBe(false);
  });

  it("trims surrounding whitespace from the name", () => {
    const result = createAgentSchema.safeParse({ ...VALID_VALUES, name: "  客服助手  " });
    expect(result.success).toBe(true);
    if (result.success) {
      expect(result.data.name).toBe("客服助手");
    }
  });

  it("rejects an agentId with invalid characters", () => {
    const result = createAgentSchema.safeParse({ ...VALID_VALUES, agentId: "BAD ID" });
    expect(result.success).toBe(false);
  });

  it("trims an agentId and validates the trimmed value", () => {
    const result = createAgentSchema.safeParse({ ...VALID_VALUES, agentId: "  support-bot  " });
    expect(result.success).toBe(true);
  });

  it("rejects more than the tag limit", () => {
    const tags = Array.from({ length: MAX_TAGS + 1 }, (_, index) => `tag${index}`).join(",");
    const result = createAgentSchema.safeParse({ ...VALID_VALUES, tags });
    expect(result.success).toBe(false);
  });

  it("rejects a non-string toolIds list (corrupt draft shape)", () => {
    const result = createAgentSchema.safeParse({ ...VALID_VALUES, toolIds: "not-an-array" });
    expect(result.success).toBe(false);
  });
});

describe("parseTags", () => {
  it("splits on commas and Chinese commas, trimming and dropping empties", () => {
    expect(parseTags(" a, b，, c ,")).toEqual(["a", "b", "c"]);
  });

  it("returns an empty array for empty input", () => {
    expect(parseTags("")).toEqual([]);
  });
});
