/**
 * Credential-free cross-tab auth signals.
 *
 * A sign-out ends the session for the whole browser profile — the refresh
 * cookie the tabs share is revoked (C-27) — while the access token each tab
 * holds in its own memory is invisible to the others. The signal says so
 * without carrying any credential, and persists nothing; a tab that misses it
 * still converges on its next request, when the refresh fails and the
 * interceptor sends it to the login page.
 */

const AUTH_CHANNEL_NAME = "eap-auth";

export type AuthSignal = "sign-out";

type AuthSignalListener = (signal: AuthSignal) => void;

const listeners = new Set<AuthSignalListener>();
let channel: BroadcastChannel | null = null;

/**
 * One channel per tab. `BroadcastChannel` never delivers a message back to the
 * object that posted it, so a shared instance keeps this tab's own signals out
 * of its listeners without putting a sender id on the wire.
 */
function getChannel(): BroadcastChannel | null {
  if (typeof window === "undefined" || typeof BroadcastChannel === "undefined") {
    return null;
  }
  if (channel === null) {
    channel = new BroadcastChannel(AUTH_CHANNEL_NAME);
    channel.onmessage = (event: MessageEvent<AuthSignal>) => {
      for (const listener of listeners) {
        listener(event.data);
      }
    };
  }
  return channel;
}

/** Tell the profile's other tabs what happened here (best effort). */
export function publishAuthSignal(signal: AuthSignal): void {
  getChannel()?.postMessage(signal);
}

/** Run `listener` for signals a peer tab published; returns the unsubscribe. */
export function subscribeAuthSignal(listener: AuthSignalListener): () => void {
  if (getChannel() === null) {
    return () => {};
  }
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
