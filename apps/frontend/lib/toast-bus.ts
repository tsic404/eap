export type ToastType = "success" | "error" | "warning" | "info";

export interface ToastOptions {
  type?: ToastType;
  title: string;
  description?: string;
  duration?: number;
}

const TOAST_EVENT = "eap:toast";

/**
 * Fire a toast from non-React code (e.g. the http-client interceptor).
 * `ToastProvider` subscribes to the same event to render it.
 */
export function toast(options: ToastOptions): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(
    new CustomEvent<ToastOptions>(TOAST_EVENT, { detail: options }),
  );
}

/** Subscribe to imperative `toast()` calls. Returns an unsubscribe function. */
export function subscribeToToasts(
  listener: (options: ToastOptions) => void,
): () => void {
  const onEvent = (event: Event) => {
    listener((event as CustomEvent<ToastOptions>).detail);
  };
  window.addEventListener(TOAST_EVENT, onEvent);
  return () => window.removeEventListener(TOAST_EVENT, onEvent);
}
