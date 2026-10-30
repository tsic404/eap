import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const { refreshAccessTokenMock, getAccessTokenMock, replaceMock, capabilitiesMock } = vi.hoisted(
  () => ({
    refreshAccessTokenMock: vi.fn(),
    getAccessTokenMock: vi.fn(),
    replaceMock: vi.fn(),
    capabilitiesMock: vi.fn(),
  }),
);

vi.mock("@/lib/refresh-channel", () => ({
  refreshAccessToken: refreshAccessTokenMock,
}));

vi.mock("@/lib/token-store", () => ({
  getAccessToken: getAccessTokenMock,
}));

vi.mock("@/lib/auth-service", () => ({
  fetchAuthCapabilities: capabilitiesMock,
}));

vi.mock("next/navigation", () => {
  // A stable instance, mirroring Next's own router identity, so an effect that
  // depends on it does not re-run on every render.
  const router = { replace: replaceMock };
  return { useRouter: () => router };
});

vi.mock("./password-change-form", () => ({
  PasswordChangeForm: () => <form>改密表单</form>,
}));

import { renderWithIntl } from "../test-utils";
import { PasswordChangePage } from "./password-change-page";

beforeEach(() => {
  refreshAccessTokenMock.mockReset();
  getAccessTokenMock.mockReset();
  replaceMock.mockReset();
  capabilitiesMock.mockReset();
});

describe("PasswordChangePage session gate", () => {
  it("shows the form when an access token is already held", async () => {
    getAccessTokenMock.mockReturnValue("access-token");

    renderWithIntl(<PasswordChangePage />);

    await waitFor(() => expect(screen.getByText("改密表单")).toBeTruthy());
    expect(refreshAccessTokenMock).not.toHaveBeenCalled();
    expect(replaceMock).not.toHaveBeenCalled();
  });

  it("mints a token through the refresh channel when none is held", async () => {
    getAccessTokenMock.mockReturnValue(null);
    refreshAccessTokenMock.mockResolvedValue({ token: "fresh-token", sessionEnded: false });

    renderWithIntl(<PasswordChangePage />);

    await waitFor(() => expect(screen.getByText("改密表单")).toBeTruthy());
    expect(refreshAccessTokenMock).toHaveBeenCalledTimes(1);
  });

  it("sends an unauthenticated visit to the login page", async () => {
    getAccessTokenMock.mockReturnValue(null);
    refreshAccessTokenMock.mockResolvedValue({ token: null, sessionEnded: true });

    renderWithIntl(<PasswordChangePage />);

    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("改密表单")).toBeNull();
  });

  it("holds the page when the refresh ended inside the conflict window", async () => {
    // Unresolved, not refused: the credential was never rejected, so the page
    // neither signs out nor opens a form on a session it cannot renew.
    getAccessTokenMock.mockReturnValue(null);
    refreshAccessTokenMock.mockResolvedValue({ token: null, sessionEnded: false });

    renderWithIntl(<PasswordChangePage />);

    await waitFor(() => expect(refreshAccessTokenMock).toHaveBeenCalledTimes(1));
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(replaceMock).not.toHaveBeenCalled();
    expect(screen.queryByText("改密表单")).toBeNull();
  });

  it("renders the form when the deployment reports local login closed", async () => {
    getAccessTokenMock.mockReturnValue("access-token");
    capabilitiesMock.mockResolvedValue({ localLoginEnabled: false });

    renderWithIntl(<PasswordChangePage />);

    await waitFor(() => expect(screen.getByText("改密表单")).toBeTruthy());
    // A closed capability stops login and admin-side maintenance, never the
    // signed-in account's own password change (C-110).
    expect(capabilitiesMock).not.toHaveBeenCalled();
  });
});
