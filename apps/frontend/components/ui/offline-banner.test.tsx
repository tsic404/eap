import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { OfflineBanner } from "./offline-banner";

describe("OfflineBanner", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the offline banner, then recovers and auto-dismisses", () => {
    render(<OfflineBanner />);

    act(() => {
      window.dispatchEvent(new Event("offline"));
    });

    expect(screen.getByText(/网络连接已断开/)).toBeTruthy();

    act(() => {
      window.dispatchEvent(new Event("online"));
    });

    expect(screen.getByText(/已恢复连接/)).toBeTruthy();

    act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(screen.queryByText(/已恢复连接/)).toBeNull();
  });
});
