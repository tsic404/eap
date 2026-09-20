// P1 baseline: chat first-token latency (POST /api/conversations/{id}/messages).
//
// Prerequisites (see docs/perf-baseline.md for the full runbook):
// - Backend up and Dify reachable (`GET /api/health/ready` → 200).
// - The target agent bound to a Dify app (its `dify_api_key` is stored in the
//   backend, not injected here).
// - A live conversation + agent id for the authenticated tenant.
//
//   k6 run k6/first-token.js -e ACCESS_TOKEN=... -e CONVERSATION_ID=... -e AGENT_ID=... \
//     [-e BASE_URL=http://localhost/api]
//
// Gate: P95 < 1500ms. The streaming endpoint keeps the response open across an
// SSE stream, so the time-to-first-byte (`response.timings.waiting`) is used as
// the first-token proxy — k6 buffers the body and cannot observe the first
// `data:` frame in isolation.
//
// The conversation route is rate-limited to 20 req/min per user, so a single VU
// with `sleep(3)` (≈13–20 req/min including request time) stays under the cap.
// Requires k6 ≥ 0.40 (`response.status` is a number).

import http from "k6/http";
import { check, sleep } from "k6";
import { Trend } from "k6/metrics";

const firstTokenLatency = new Trend("first_token_latency", true);

export const options = {
  scenarios: {
    chat: {
      executor: "constant-vus",
      vus: 1,
      duration: "2m",
    },
  },
  thresholds: {
    first_token_latency: ["p(95)<1500"],
  },
};

const BASE_URL = __ENV.BASE_URL || "http://localhost/api";
const TOKEN = __ENV.ACCESS_TOKEN || "";
const CONVERSATION_ID = __ENV.CONVERSATION_ID || "";
const AGENT_ID = __ENV.AGENT_ID || "";

/** Abort with a clear message when the backend (and Dify) is not ready. */
export function setup() {
  const ready = http.get(`${BASE_URL}/health/ready`);
  if (ready.status !== 200) {
    throw new Error(
      `backend/Dify not ready (status ${ready.status}); ensure backend + Dify are up before running the baseline`,
    );
  }
  return {};
}

export default function () {
  const payload = JSON.stringify({ query: "你好", agentId: AGENT_ID });
  const response = http.post(
    `${BASE_URL}/conversations/${CONVERSATION_ID}/messages`,
    payload,
    {
      headers: {
        Authorization: `Bearer ${TOKEN}`,
        "Content-Type": "application/json",
      },
    },
  );
  firstTokenLatency.add(response.timings.waiting);
  check(response, {
    "status 200": (res) => res.status === 200,
    "event-stream": (res) =>
      (res.headers["Content-Type"] ?? "").includes("text/event-stream"),
  });
  sleep(3);
}
