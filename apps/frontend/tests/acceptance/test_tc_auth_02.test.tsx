/**
 * TC-auth-02 — a signed-in visitor skips the identity provider, and an expired
 * access token is renewed without the user noticing (AC-02, C-25, C-15).
 *
 * The cases drive the real refresh channel (its network edge — `fetch` — is
 * stubbed) and the real API client (its adapter is stubbed), so the observable
 * facts are the page each answer lands on: the workspace, or the login page.
 *
 * Evidence boundary: jsdom does not perform navigations. "Lands on" is read
 * from the router call the page makes and from the content that survives on the
 * protected path; the browser-side replay of the hard-navigation case is
 * `e2e/journeys/tc-auth-02-session-resume.spec.ts`.
 */
import { AxiosError } from "axios";
import type { AxiosResponse, InternalAxiosRequestConfig } from "axios";
import { screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const { replaceMock } = vi.hoisted(() => ({ replaceMock: vi.fn() }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: replaceMock, push: replaceMock }),
}));

import { AuthGuard } from "@/components/auth/auth-guard";
import { AuthProvider } from "@/components/auth/auth-context";
import { LoginPage } from "@/components/auth/login-page";
import { renderWithIntl } from "@/components/test-utils";
import { apiClient } from "@/lib/http-client";
import { getAccessToken, setAccessToken } from "@/lib/token-store";

export const TC_ID = "TC-auth-02";

const ALICE = {
  id: "u1",
  tenantId: "t1",
  email: "alice@acme.com",
  name: "Alice",
  department: null,
  role: "agent_admin",
  avatarText: null,
};

const fetchMock = vi.fn();

// The stamp a document leaves while its rotation is in flight; the bootstrap of
// the next document reads it out of the storage the browser shares across
// navigations.
const LEASE_KEY = "eap-auth-refresh-lease";

interface AdapterReply {
  status: number;
  body: unknown;
}

/** Answer one API path as the backend would; the access token it saw is recorded. */
type AdapterHandler = (request: {
  path: string;
  authorization: string | undefined;
}) => AdapterReply;

function installAdapter(handler: AdapterHandler): void {
  apiClient.defaults.adapter = async (config: InternalAxiosRequestConfig) => {
    const reply = handler({
      path: `${config.baseURL ?? ""}${config.url ?? ""}`,
      authorization: config.headers.get("Authorization") as string | undefined,
    });
    const response = {
      data: reply.body,
      status: reply.status,
      statusText: "",
      headers: {},
      config,
    } as AxiosResponse;
    if (reply.status >= 400) {
      throw new AxiosError("request rejected", String(reply.status), config, null, response);
    }
    return response;
  };
}

function jsonResponse(status: number, body: unknown): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}

function tokenResponse(accessToken: string): Response {
  return jsonResponse(200, { code: 0, data: { accessToken, expiresIn: 900 }, message: "" });
}

function conflictResponse(): Response {
  return jsonResponse(409, {
    error: { code: "TOKEN_REFRESH_IN_PROGRESS", message: "Refresh in progress" },
  });
}

function profileEnvelope() {
  return { code: 0, data: ALICE, message: "" };
}

function capabilitiesEnvelope() {
  return { code: 0, data: { localLoginEnabled: false }, message: "" };
}

function protectedPage() {
  return renderWithIntl(
    <AuthProvider>
      <AuthGuard>
        <p>工作区内容</p>
      </AuthGuard>
    </AuthProvider>,
  );
}

beforeEach(() => {
  replaceMock.mockReset();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
  setAccessToken(null);
  document.cookie = "";
  sessionStorage.clear();
  installAdapter(({ path }) => {
    if (path === "/api/me") return { status: 200, body: profileEnvelope() };
    if (path === "/api/auth/capabilities") return { status: 200, body: capabilitiesEnvelope() };
    return { status: 404, body: {} };
  });
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("TC-auth-02 signed-in visitor", () => {
  it("enters the workspace from the login page without opening the identity provider", async () => {
    fetchMock.mockResolvedValue(tokenResponse("fresh-token"));

    renderWithIntl(
      <AuthProvider>
        <LoginPage />
      </AuthProvider>,
    );

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/user"));
    // The enterprise entry was never taken: no navigation to the provider.
    expect(screen.getByRole("button", { name: "SSO 登录" })).toBeTruthy();
  });
});

describe("TC-auth-02 refresh conflict", () => {
  it("keeps the page when the first refresh is answered with TOKEN_REFRESH_IN_PROGRESS", async () => {
    fetchMock
      .mockResolvedValueOnce(conflictResponse())
      .mockResolvedValueOnce(tokenResponse("successor-token"));

    protectedPage();

    expect(await screen.findByText("工作区内容")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(replaceMock).not.toHaveBeenCalledWith("/login");
    expect(getAccessToken()).toBe("successor-token");
  });

  it("keeps the session and its role when the rotation window runs out", async () => {
    // The window is a conflict, not a refusal: another holder rotated and the
    // successor may still land. Ending the session here is what sent reloads
    // to the login page while the backend still held it (C-25).
    document.cookie = "eap_role=agent_admin; path=/";
    fetchMock.mockImplementation(async () => conflictResponse());

    protectedPage();

    // Wait for the bounded retry sequence to settle before judging the session:
    // a longer loop would outlive C-25's 30-second window and be answered with
    // a whole-family revocation.
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(6), { timeout: 15_000 });
    expect(replaceMock).not.toHaveBeenCalledWith("/login");
    expect(document.cookie).toContain("eap_role=agent_admin");
    expect(screen.queryByText("工作区内容")).toBeNull();
  }, 15_000);

  it("drops the role and sends the user to login when the backend refuses the session", async () => {
    document.cookie = "eap_role=agent_admin; path=/";
    fetchMock.mockResolvedValue(
      jsonResponse(401, { error: { code: "TOKEN_REUSE_DETECTED", message: "Refresh token reuse" } }),
    );

    protectedPage();

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    expect(document.cookie).not.toContain("eap_role=agent_admin");
  });
});

describe("TC-auth-02 silent renewal", () => {
  it("renews an expired access token and replays the rejected request", async () => {
    const seenTokens: Array<string | undefined> = [];
    installAdapter(({ path, authorization }) => {
      if (path === "/api/me") return { status: 200, body: profileEnvelope() };
      seenTokens.push(authorization);
      // The first business call presents the stale token; the replay carries
      // the one the refresh channel just minted.
      return authorization === "Bearer stale-token"
        ? { status: 401, body: { error: { code: "TOKEN_EXPIRED" } } }
        : { status: 200, body: { code: 0, data: { ok: true }, message: "" } };
    });
    setAccessToken("stale-token");
    fetchMock.mockResolvedValue(tokenResponse("renewed-token"));

    protectedPage();
    expect(await screen.findByText("工作区内容")).toBeTruthy();

    const response = await apiClient.get("/agents");

    expect(response.data.data).toEqual({ ok: true });
    expect(seenTokens).toEqual(["Bearer stale-token", "Bearer renewed-token"]);
    expect(replaceMock).not.toHaveBeenCalledWith("/login");
  });
});

describe("TC-auth-02 cross-document rotation", () => {
  it("enters the workspace when the previous document's rotation is still unaccounted", async () => {
    // A document destroyed with its refresh in flight (reload, navigation) is
    // gone but its rotation may still be landing. The bootstrap must wait it
    // out: presenting the credential early is answered with a conflict.
    sessionStorage.setItem(LEASE_KEY, JSON.stringify({ owner: "predecessor", at: Date.now() - 50 }));
    fetchMock.mockResolvedValueOnce(tokenResponse("recovered-token"));

    protectedPage();

    await new Promise((resolve) => setTimeout(resolve, 300));
    expect(fetchMock).not.toHaveBeenCalled();

    expect(await screen.findByText("工作区内容")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(document.cookie).toContain("eap_role=agent_admin");
    expect(replaceMock).not.toHaveBeenCalledWith("/login");
  });

  it("keeps the role of the session when a conflict is retried into a success", async () => {
    fetchMock
      .mockResolvedValueOnce(conflictResponse())
      .mockResolvedValueOnce(tokenResponse("successor-token"));

    protectedPage();

    expect(await screen.findByText("工作区内容")).toBeTruthy();
    // C-25 keeps the session through the conflict window: the gate still sees
    // the signed-in role on the next navigation.
    expect(document.cookie).toContain("eap_role=agent_admin");
    expect(replaceMock).not.toHaveBeenCalledWith("/login");
  });
});
