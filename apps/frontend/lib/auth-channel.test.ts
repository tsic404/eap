import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AuthSignal } from "./auth-channel";

/**
 * A fake `BroadcastChannel` that keeps the delivery rule the store relies on:
 * a message reaches every other channel of the same name, never the channel
 * that posted it. Two `loadTab()` copies then behave like two tabs.
 */
class FakeBroadcastChannel {
  static readonly byName = new Map<string, Set<FakeBroadcastChannel>>();

  onmessage: ((event: MessageEvent) => void) | null = null;

  constructor(private readonly name: string) {
    const peers = FakeBroadcastChannel.byName.get(name) ?? new Set<FakeBroadcastChannel>();
    peers.add(this);
    FakeBroadcastChannel.byName.set(name, peers);
  }

  postMessage(data: unknown): void {
    for (const peer of FakeBroadcastChannel.byName.get(this.name) ?? []) {
      if (peer !== this) peer.onmessage?.({ data } as MessageEvent);
    }
  }

  close(): void {
    FakeBroadcastChannel.byName.get(this.name)?.delete(this);
  }
}

/**
 * A fresh module instance, i.e. a tab that just loaded the app. Two tabs in
 * one process can only be told apart across a module-loading boundary, so this
 * deliberately re-imports instead of using a static import.
 */
async function loadTab() {
  vi.resetModules();
  return await import("./auth-channel");
}

function recordSignals(): { signals: AuthSignal[]; listener: (signal: AuthSignal) => void } {
  const signals: AuthSignal[] = [];
  return { signals, listener: (signal) => signals.push(signal) };
}

beforeEach(() => {
  FakeBroadcastChannel.byName.clear();
  vi.stubGlobal("BroadcastChannel", FakeBroadcastChannel);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("auth-channel", () => {
  it("delivers a peer tab's sign-out to this tab's subscriber", async () => {
    const peerTab = await loadTab();
    const thisTab = await loadTab();
    const { signals, listener } = recordSignals();
    thisTab.subscribeAuthSignal(listener);

    peerTab.publishAuthSignal("sign-out");

    expect(signals).toEqual(["sign-out"]);
  });

  it("does not hand a tab its own signal back", async () => {
    const thisTab = await loadTab();
    const { signals, listener } = recordSignals();
    thisTab.subscribeAuthSignal(listener);

    thisTab.publishAuthSignal("sign-out");

    expect(signals).toEqual([]);
  });

  it("stops delivering after the subscriber unsubscribes", async () => {
    const peerTab = await loadTab();
    const thisTab = await loadTab();
    const { signals, listener } = recordSignals();
    const unsubscribe = thisTab.subscribeAuthSignal(listener);

    unsubscribe();
    peerTab.publishAuthSignal("sign-out");

    expect(signals).toEqual([]);
  });

  it("degrades to a no-op when the browser has no BroadcastChannel", async () => {
    vi.stubGlobal("BroadcastChannel", undefined);
    const thisTab = await loadTab();
    const { signals, listener } = recordSignals();

    const unsubscribe = thisTab.subscribeAuthSignal(listener);

    expect(() => thisTab.publishAuthSignal("sign-out")).not.toThrow();
    expect(signals).toEqual([]);
    expect(() => unsubscribe()).not.toThrow();
  });
});
