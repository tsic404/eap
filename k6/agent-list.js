// P1 baseline: Agent list endpoint latency (GET /api/agents).
//
//   k6 run k6/agent-list.js -e ACCESS_TOKEN=... [-e BASE_URL=http://localhost/api]
//
// Gate: P95 < 200ms (matches the backend pytest perf gate's target, tightened
// from the CI regression threshold of 240ms). Requires k6 ≥ 0.40
// (`response.status` is a number, compared against the numeric literal 200).

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";

const agentListLatency = new Trend("agent_list_latency", true);

export const options = {
  scenarios: {
    agents: {
      executor: "ramping-vus",
      startVUs: 1,
      stages: [
        { duration: "30s", target: 20 },
        { duration: "1m", target: 20 },
        { duration: "30s", target: 0 },
      ],
    },
  },
  thresholds: {
    agent_list_latency: ["p(95)<200"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost/api";
const TOKEN = __ENV.ACCESS_TOKEN || "";

export default function () {
  const response = http.get(`${BASE_URL}/agents`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  agentListLatency.add(response.timings.duration);
  check(response, { "status 200": (res) => res.status === 200 });
  sleep(1);
}
