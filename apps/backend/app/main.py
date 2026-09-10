"""EAP backend application entrypoint.

Minimal skeleton: application instance and health probes. Config management,
middleware pipeline, exception handlers and the response transform wrapper are
added in the application-skeleton task (T2-BE).
"""

from fastapi import FastAPI

APP_VERSION = "0.1.0"

app = FastAPI(
    title="EAP Backend",
    description="Enterprise Agent Platform backend API",
    version=APP_VERSION,
)


@app.get("/api/health", tags=["health"])
def health() -> dict[str, str]:
    """Basic service info."""
    return {"service": "eap-backend", "version": APP_VERSION, "status": "ok"}


@app.get("/api/health/live", tags=["health"])
def health_live() -> dict[str, str]:
    """Liveness probe — returns 200 while the process is up."""
    return {"status": "ok"}
