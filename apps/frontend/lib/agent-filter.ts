import type { Agent } from "./agent-types";

/** Distinct, non-empty categories across the given agents, in first-seen order. */
export function deriveCategories(agents: Agent[]): string[] {
  const seen = new Set<string>();
  const categories: string[] = [];
  for (const agent of agents) {
    const category = agent.category;
    if (category && !seen.has(category)) {
      seen.add(category);
      categories.push(category);
    }
  }
  return categories;
}

/** Filter agents by a case-insensitive search term and an exact category. */
export function filterAgents(
  agents: Agent[],
  search: string,
  category: string | null,
): Agent[] {
  const term = search.trim().toLowerCase();
  return agents.filter((agent) => {
    const matchesCategory = category === null || agent.category === category;
    const matchesSearch =
      term === "" ||
      agent.name.toLowerCase().includes(term) ||
      (agent.description ?? "").toLowerCase().includes(term);
    return matchesCategory && matchesSearch;
  });
}
