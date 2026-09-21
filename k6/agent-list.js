// P1 baseline: Agent list endpoint latency (GET /api/agents).
//
//   k6 run k6/agent-list.js -e ACCESS_TOKEN=... [-e BASE_URL=http://localhost/api]
//
// Gate: P95 < 330ms (concurrent, 20-VU ramp). The authoritative run is on a
// fixed-resource CI runner (scripts/k6-perf-gate.sh + the `k6` job in
// .github/workflows/ci.yml), because the shared dev host is too noisy to gate
// reproducibly: other agents' jobs spike it to load 16+ and shift the *whole*
// latency distribution (QA measured p(90)=511ms under such contention), so no
// static threshold stays meaningful there. Local runs are tuning-only. The
// 330ms gate is looser than the backend pytest perf gate (P95 < 240ms in
// ci.yml) because they measure different loads: pytest issues 50 sequential
// requests (240ms = the 200ms baseline plus a 20% regression allowance, see
// apps/backend/tests/integration/test_performance.py), while this script drives
// 20 concurrent VUs whose latency includes connection-pool contention.
//
// setup() warms the backend's connection pool before the ramp so the first
// ramped requests skip the cold-connection cost; those timings are discarded.
// Requires k6 ≥ 0.40 (`response.status` is a number, so `res.status === 200`
// compares against the numeric HTTP status).

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";

const agentListLatency = new Trend("agent_list_latency", true);

const BASE_URL = __ENV.BASE_URL || "http://localhost/api";
const TOKEN = __ENV.ACCESS_TOKEN || "";

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
    agent_list_latency: ["p(95)<330"],
  },
};

/** Warm the backend's connection pool; timings are discarded, not gated. */
export function setup() {
  const warm = http.batch(
    Array.from({ length: 20 }, () => ({
      method: "GET",
      url: `${BASE_URL}/agents`,
      params: { headers: { Authorization: `Bearer ${TOKEN}` } },
    })),
  );
  for (const response of warm) {
    if (response.status !== 200) {
      throw new Error(`warm-up request failed (status ${response.status})`);
    }
  }
  return {};
}

export default function () {
  const response = http.get(`${BASE_URL}/agents`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  agentListLatency.add(response.timings.duration);
  check(response, { "status 200": (res) => res.status === 200 });
  sleep(1);
}
