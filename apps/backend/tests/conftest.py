"""Shared pytest fixtures."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.main import create_app


class EchoItem(BaseModel):
    name: str


@pytest.fixture
def client() -> Iterator[TestClient]:
    app: FastAPI = create_app()

    @app.get("/api/test/echo")
    def echo() -> dict[str, str]:
        return {"hello": "world"}

    @app.get("/api/test/boom")
    def boom() -> None:
        raise RuntimeError("boom")

    @app.get("/api/test/missing")
    def missing() -> None:
        raise HTTPException(status_code=404, detail="resource not found")

    @app.post("/api/test/validate")
    def validate(item: EchoItem) -> EchoItem:
        return item

    # raise_server_exceptions=False so the global exception handlers' HTTP
    # responses are observable instead of being re-raised by the server error
    # middleware (which always re-raises after sending the 500 response).
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
