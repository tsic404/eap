"""Performance gate: Agent list P95 must stay under 240ms (architecture §33.7.4).

The 240ms CI threshold is the 200ms baseline plus a 20% regression allowance;
crossing it blocks the PR. The test is marker-gated (``@pytest.mark.perf``) so
it runs in the dedicated perf step, not the default unit/integration run.
"""

from __future__ import annotations

import math
import time

import pytest

# CI fail threshold = baseline (200ms) + 20% degradation.
P95_LIMIT_MS = 240.0
_SAMPLES = 50


def _p95(latencies: list[float]) -> float:
    """Nearest-rank 95th percentile: the ``ceil(0.95*N)`` smallest sample."""
    ordered = sorted(latencies)
    if not ordered:
        return 0.0
    index = math.ceil(len(ordered) * 0.95) - 1
    return ordered[index]


@pytest.mark.perf
@pytest.mark.asyncio
async def test_agent_list_p95_under_240ms(api) -> None:
    tenant, user = await api.seed(role="agent_admin")
    for i in range(5):
        await api.seed_agent(tenant, agent_id=f"agent-{i}", status="published")

    headers = api.auth(user, tenant)
    latencies: list[float] = []
    for _ in range(_SAMPLES):
        started = time.perf_counter()
        resp = await api.client.get("/api/agents", headers=headers)
        assert resp.status_code == 200
        latencies.append((time.perf_counter() - started) * 1000)

    p95 = _p95(latencies)
    assert p95 < P95_LIMIT_MS, f"Agent list P95 {p95:.1f}ms exceeds {P95_LIMIT_MS}ms"
