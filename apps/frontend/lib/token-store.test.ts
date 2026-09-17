import { beforeEach, describe, expect, it } from "vitest";

import { clearAccessToken, getAccessToken, setAccessToken } from "./token-store";

const COOKIE = "eap_access_token";

function clearCookie() {
  document.cookie = `${COOKIE}=; path=/; Max-Age=0`;
}

beforeEach(() => {
  clearAccessToken();
  clearCookie();
});

describe("token-store", () => {
  it("reads the access token from the cookie", () => {
    document.cookie = `${COOKIE}=abc123; path=/`;
    expect(getAccessToken()).toBe("abc123");
  });

  it("caches the cookie value in memory", () => {
    document.cookie = `${COOKIE}=abc123; path=/`;
    getAccessToken();
    clearCookie();
    expect(getAccessToken()).toBe("abc123");
  });

  it("returns null when neither memory nor cookie holds a token", () => {
    expect(getAccessToken()).toBeNull();
  });

  it("clearAccessToken resets the memory cache", () => {
    setAccessToken("xyz");
    clearAccessToken();
    expect(getAccessToken()).toBeNull();
  });

  it("treats a malformed percent-encoded cookie as absent", () => {
    document.cookie = `${COOKIE}=%E0%A4%A; path=/`;
    expect(getAccessToken()).toBeNull();
  });

  it("expires the cookie when clearing the access token", () => {
    document.cookie = `${COOKIE}=abc123; path=/`;
    clearAccessToken();
    expect(document.cookie).toBe("");
  });
});
