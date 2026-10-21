import { describe, expect, it, vi } from "vitest";

const { getMock, postMock } = vi.hoisted(() => ({
  getMock: vi.fn(),
  postMock: vi.fn(),
}));

vi.mock("./http-client", () => ({
  apiClient: {
    get: getMock,
    post: postMock,
  },
}));

import {
  changePassword,
  fetchAuthCapabilities,
  isPasswordChangeRequiredError,
  localLoginErrorKey,
  loginWithLocalAccount,
  passwordChangeErrorKey,
} from "./auth-service";

function envelope<T>(data: T) {
  return { code: 0, data, message: "" };
}

function apiError(status: number, code?: string): unknown {
  return {
    isAxiosError: true,
    response: { status, data: code === undefined ? undefined : { error: { code } } },
    message: "Request failed",
  };
}

describe("fetchAuthCapabilities", () => {
  it("reads the local-login flag from the public capability read point", async () => {
    getMock.mockResolvedValue({ data: envelope({ localLoginEnabled: true }) });

    await expect(fetchAuthCapabilities()).resolves.toEqual({ localLoginEnabled: true });
    expect(getMock).toHaveBeenCalledWith("/auth/capabilities");
  });

  it("reports the capability as closed when the deployment declares it off", async () => {
    getMock.mockResolvedValue({ data: envelope({ localLoginEnabled: false }) });

    await expect(fetchAuthCapabilities()).resolves.toEqual({ localLoginEnabled: false });
  });
});

describe("loginWithLocalAccount", () => {
  it("posts the email and password to the local login route", async () => {
    postMock.mockResolvedValue({ data: envelope({ mustChangePassword: false }) });

    await expect(loginWithLocalAccount("carol@acme.com", "Local-Account-1")).resolves.toEqual({
      mustChangePassword: false,
    });
    expect(postMock).toHaveBeenCalledWith("/auth/login/local", {
      email: "carol@acme.com",
      password: "Local-Account-1",
    });
  });
});

describe("localLoginErrorKey", () => {
  it.each([
    ["RATE_LIMITED", "errors.rateLimited"],
    ["LOGIN_LOCKED", "errors.locked"],
    ["INVALID_CREDENTIALS", "errors.invalidCredentials"],
    ["TENANT_NOT_FOUND", "errors.tenantNotFound"],
    ["TENANT_AMBIGUOUS", "errors.tenantAmbiguous"],
    ["VALIDATION_ERROR", "errors.validation"],
    ["NOT_FOUND", "errors.capabilityClosed"],
  ])("maps the %s rejection to %s", (code, key) => {
    expect(localLoginErrorKey(apiError(429, code))).toBe(key);
  });

  it("falls back to the generic copy when the backend is unreachable", () => {
    expect(localLoginErrorKey(new Error("network down"))).toBe("errors.generic");
  });

  it("falls back to the generic copy for an unmapped code", () => {
    expect(localLoginErrorKey(apiError(500, "INTERNAL_ERROR"))).toBe("errors.generic");
    expect(localLoginErrorKey(apiError(500))).toBe("errors.generic");
  });
});

describe("changePassword", () => {
  it("posts both passwords to the change route", async () => {
    postMock.mockResolvedValue({ data: envelope({ message: "密码已修改" }) });

    await changePassword("Local-Account-1", "Rotated-Account-2");

    expect(postMock).toHaveBeenCalledWith("/auth/password", {
      currentPassword: "Local-Account-1",
      newPassword: "Rotated-Account-2",
    });
  });
});

describe("passwordChangeErrorKey", () => {
  it.each([
    ["401 INVALID_CREDENTIALS", 401, "INVALID_CREDENTIALS", "errors.invalidCurrent"],
    ["422 VALIDATION_ERROR", 422, "VALIDATION_ERROR", "errors.validation"],
  ])("maps the %s rejection to %s", (_case, status, code, key) => {
    expect(passwordChangeErrorKey(apiError(status, code))).toBe(key);
  });

  it("falls back to the generic copy when the backend is unreachable", () => {
    expect(passwordChangeErrorKey(new Error("network down"))).toBe("errors.generic");
  });

  it("falls back to the generic copy for an unmapped code", () => {
    expect(passwordChangeErrorKey(apiError(500, "INTERNAL_ERROR"))).toBe("errors.generic");
  });
});

describe("isPasswordChangeRequiredError", () => {
  it("recognises the forced-change rejection", () => {
    expect(isPasswordChangeRequiredError(apiError(403, "PASSWORD_CHANGE_REQUIRED"))).toBe(true);
  });

  it("does not treat another 403 as the forced-change rejection", () => {
    expect(isPasswordChangeRequiredError(apiError(403, "FORBIDDEN"))).toBe(false);
  });
});
