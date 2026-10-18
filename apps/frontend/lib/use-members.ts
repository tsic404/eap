"use client";

import useSWR from "swr";

import { listMembers } from "./member-service";
import type { MemberListParams } from "./member-types";

/** SWR-backed roster page; the key carries every filter so a change refetches. */
export function useMembers(params: MemberListParams) {
  return useSWR(
    ["members", params.page, params.pageSize, params.search, params.role, params.status],
    () => listMembers(params),
  );
}
