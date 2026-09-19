"use client";

import useSWR from "swr";

import { getRunLogDetail } from "./run-log-service";

/** SWR-backed single run-log detail (trace summary plus sub-records). */
export function useRunLogDetail(traceId: string | undefined) {
  return useSWR(traceId ? `run-log:${traceId}` : null, () =>
    getRunLogDetail(traceId!),
  );
}
