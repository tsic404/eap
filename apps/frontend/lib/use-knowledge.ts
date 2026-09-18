"use client";

import useSWR from "swr";

import {
  getKnowledgeBase,
  listDocuments,
  listKnowledgeBases,
} from "./knowledge-service";
import type { DocumentListResponse } from "./knowledge-types";

/** SWR-backed knowledge-base page. */
export function useKnowledgeBases(page = 1, limit = 20) {
  const key = `knowledge-bases:${page}:${limit}`;
  return useSWR(key, () => listKnowledgeBases(page, limit));
}

/** SWR-backed single knowledge base. */
export function useKnowledgeBase(kbId: string | undefined) {
  return useSWR(kbId ? `knowledge-base:${kbId}` : null, () =>
    getKnowledgeBase(kbId!),
  );
}

/**
 * SWR-backed document listing for a knowledge base. While any document is
 * still `indexing`, poll every 3s so the terminal state surfaces without a
 * manual refresh; polling stops once nothing is indexing.
 */
export function useDocuments(kbId: string | undefined, page = 1, limit = 20) {
  const key = kbId ? `documents:${kbId}:${page}:${limit}` : null;
  return useSWR(key, () => listDocuments(kbId!, page, limit), {
    refreshInterval: (data?: DocumentListResponse) =>
      data?.items.some((document) => document.status === "indexing") ? 3000 : 0,
  });
}
