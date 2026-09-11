import { describe, expect, it } from "vitest";

import { isValidAgentId, isValidEmail, isValidUrl } from "./validators";

describe("isValidEmail", () => {
  it("accepts a normal email", () => {
    expect(isValidEmail("user@example.com")).toBe(true);
  });

  it("rejects a missing domain", () => {
    expect(isValidEmail("user@")).toBe(false);
  });

  it("rejects a missing @", () => {
    expect(isValidEmail("userexample.com")).toBe(false);
  });
});

describe("isValidUrl", () => {
  it("accepts an https URL", () => {
    expect(isValidUrl("https://example.com/path")).toBe(true);
  });

  it("rejects plain text", () => {
    expect(isValidUrl("not a url")).toBe(false);
  });
});

describe("isValidAgentId", () => {
  it("accepts lowercase words and hyphens", () => {
    expect(isValidAgentId("my-agent-1")).toBe(true);
  });

  it("rejects uppercase and symbols", () => {
    expect(isValidAgentId("My Agent!")).toBe(false);
  });

  it("rejects too-short ids", () => {
    expect(isValidAgentId("ab")).toBe(false);
  });
});
