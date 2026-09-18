"use client";

import useSWR from "swr";

import { getTool, listTools } from "./tool-service";

/** SWR-backed tool list. */
export function useTools() {
  return useSWR("tools", () => listTools());
}

/** SWR-backed single tool. */
export function useTool(toolId: string | undefined) {
  return useSWR(toolId ? `tool:${toolId}` : null, () => getTool(toolId!));
}
