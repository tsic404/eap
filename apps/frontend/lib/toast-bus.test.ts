import { describe, expect, it, vi } from "vitest";

import { subscribeToToasts, toast } from "./toast-bus";

describe("toast-bus", () => {
  it("delivers imperative toasts to subscribers", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToToasts(listener);

    toast({ type: "error", title: "无权限访问" });

    expect(listener).toHaveBeenCalledWith({ type: "error", title: "无权限访问" });
    unsubscribe();
  });

  it("stops delivering after unsubscribe", () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToToasts(listener);
    unsubscribe();

    toast({ title: "不会送达" });

    expect(listener).not.toHaveBeenCalled();
  });
});
