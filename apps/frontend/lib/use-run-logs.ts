"use client";

import useSWR from "swr";

import { getRunLog } from "./run-log-service";

/** SWR-backed single run-log detail. */
export function useRunLog(traceId: string | undefined) {
  return useSWR(traceId ? `run-log:${traceId}` : null, () =>
    getRunLog(traceId!),
  );
}
