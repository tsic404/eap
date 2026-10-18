import { beforeEach, describe, expect, it, vi } from "vitest";

import { getAccessToken, setAccessToken } from "./token-store";

const LEGACY_COOKIE = "eap_access_token";
const SAMPLE_TOKEN = "header.payload.signature";

/** Everything a page script can read: cookies and both web storages. */
function scriptReadableState(): string {
  return [
    document.cookie,
    JSON.stringify(localStorage),
    JSON.stringify(sessionStorage),
  ].join(" | ");
}

beforeEach(() => {
  setAccessToken(null);
  document.cookie = `${LEGACY_COOKIE}=; Max-Age=0; path=/`;
  localStorage.clear();
  sessionStorage.clear();
});

describe("token-store", () => {
  it("hands back the token this tab stored in memory", () => {
    setAccessToken(SAMPLE_TOKEN);

    expect(getAccessToken()).toBe(SAMPLE_TOKEN);
  });

  it("does not survive a reload — only the refresh channel can restore it", async () => {
    setAccessToken(SAMPLE_TOKEN);

    // A page load is a module-loading boundary: only a fresh module instance
    // can tell whether the token survived, so a static import cannot stand in.
    vi.resetModules();
    const reloaded = await import("./token-store");

    expect(reloaded.getAccessToken()).toBeNull();
  });

  it("ignores an access-token cookie, whatever wrote it", () => {
    document.cookie = `${LEGACY_COOKIE}=legacy-token; path=/`;

    expect(getAccessToken()).toBeNull();
  });

  it("keeps the token out of every script-readable store", () => {
    setAccessToken(SAMPLE_TOKEN);

    expect(document.cookie).not.toContain(LEGACY_COOKIE);
    expect(document.cookie).toBe("");
    expect(localStorage.length).toBe(0);
    expect(sessionStorage.length).toBe(0);
    expect(scriptReadableState()).not.toContain("header.payload.signature");
  });

  it("leaves no copy behind when the session drops the token", () => {
    setAccessToken(SAMPLE_TOKEN);

    setAccessToken(null);

    expect(getAccessToken()).toBeNull();
    expect(scriptReadableState()).not.toContain("header.payload.signature");
  });
});
