"""One real service process for the upload acceptance cases.

The cases start this module as a subprocess so a second *OS process* carries its
own in-flight pool against the same PostgreSQL/Redis deployment. It boots the
shipped app (``app.main:app``, the same wiring a deployment uses) and adds only
the counter route the cases synchronize on; the upload path itself is untouched.
"""

from __future__ import annotations

import os

import uvicorn

from app.main import app
from tests.acceptance.pool_probe import PROBE_PATH, PoolProbe


def main() -> None:
    probe = PoolProbe(app.state.upload_slots)
    app.state.upload_slots = probe

    @app.get(PROBE_PATH)
    async def upload_pool_state() -> dict[str, int]:
        return probe.snapshot()

    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ["UPLOAD_SERVICE_PORT"]),
        log_level="warning",
    )


if __name__ == "__main__":
    main()
