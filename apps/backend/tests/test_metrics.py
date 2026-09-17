"""Prometheus metrics tests: /metrics endpoint + application metric registration."""

from fastapi.testclient import TestClient


def test_metrics_endpoint_returns_prometheus_format(client: TestClient) -> None:
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.text
    assert body.startswith("#") or "http_requests_total" in body
    assert "http_requests_total" in body
    assert "http_request_duration_seconds" in body


def test_metrics_include_application_metrics(client: TestClient) -> None:
    resp = client.get("/metrics")
    body = resp.text
    assert "dify_api_requests_total" in body
    assert "dify_api_request_duration_seconds" in body
    assert "db_pool_size" in body
    assert "db_pool_checkedout" in body
    assert "rq_audit_log_queue_depth" in body
