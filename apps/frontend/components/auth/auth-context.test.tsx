import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { refreshAccessTokenMock, apiPostMock, apiGetMock } = vi.hoisted(() => ({
  refreshAccessTokenMock: vi.fn(),
  apiPostMock: vi.fn(),
  apiGetMock: vi.fn(),
}));

vi.mock("@/lib/http-client", () => ({
  apiClient: {
    get: apiGetMock,
    post: apiPostMock,
  },
  refreshAccessToken: refreshAccessTokenMock,
}));

import { AuthProvider, useAuth } from "./auth-context";

const COOKIE = "eap_access_token";

function LogoutTrigger() {
  const { logout } = useAuth();
  return (
    <button type="button" onClick={() => void logout()}>
      logout
    </button>
  );
}

const originalLocation = window.location;

afterEach(() => {
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
});

describe("AuthProvider logout", () => {
  it("expires the access cookie and redirects when the backend logout fails", async () => {
    refreshAccessTokenMock.mockResolvedValue(null);
    apiGetMock.mockResolvedValue({ data: { code: 0, data: null, message: "" } });
    apiPostMock.mockRejectedValue(new Error("backend down"));

    const assign = vi.fn();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { ...originalLocation, assign, pathname: "/user" },
    });

    render(
      <AuthProvider>
        <LogoutTrigger />
      </AuthProvider>,
    );

    await waitFor(() => expect(refreshAccessTokenMock).toHaveBeenCalled());

    // A stale cookie left behind by a prior login must not survive logout.
    document.cookie = `${COOKIE}=stale-token; path=/`;
    fireEvent.click(screen.getByRole("button", { name: "logout" }));

    await waitFor(() => {
      expect(document.cookie).toBe("");
      expect(assign).toHaveBeenCalledWith("/login");
    });
  });
});
