"use client";

import useSWR from "swr";

import type { AgentListParams } from "./agent-types";
import { getAgent, listAgents, listAllAgents } from "./platform-service";

/**
 * SWR-backed agent list. The key is a serialized copy of `params`, so a new
 * params object with identical content reuses the cache instead of refetching.
 */
export function useAgents(params?: AgentListParams) {
  const key = params ? `agents:${JSON.stringify(params)}` : "agents";
  return useSWR(key, () => listAgents(params));
}

/** SWR-backed single agent (detail shape with bindings and recent logs). */
export function useAgent(agentId: string | undefined) {
  return useSWR(agentId ? `agent:${agentId}` : null, () => getAgent(agentId!));
}

/** SWR-backed list of every matching agent across all pages. */
export function useAllAgents(params?: Omit<AgentListParams, "page" | "pageSize">) {
  const key = `agents:all:${JSON.stringify(params ?? {})}`;
  return useSWR(key, () => listAllAgents(params));
}
