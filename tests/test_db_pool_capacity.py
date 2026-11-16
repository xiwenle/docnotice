from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from app.core.compression import SelectiveGZipMiddleware
from app.core.config import get_settings
from app.db.session import engine
from app.main import (
    _contains_pool_timeout,
    app,
    database_pool_timeout_handler,
)


def test_engine_pool_matches_capacity_configuration():
    settings = get_settings()
    assert settings.DB_POOL_SIZE >= 20
    assert settings.DB_MAX_OVERFLOW >= 20
    assert settings.DB_POOL_SIZE + settings.DB_MAX_OVERFLOW >= 40
    assert settings.DB_POOL_TIMEOUT_SECONDS <= 10
    assert engine.pool.size() == settings.DB_POOL_SIZE
    assert engine.pool._max_overflow == settings.DB_MAX_OVERFLOW


def test_connection_pool_timeout_is_retryable_503_not_500():
    test_app = FastAPI()
    test_app.add_exception_handler(SQLAlchemyTimeoutError, database_pool_timeout_handler)

    @test_app.get("/pool-timeout")
    def pool_timeout():
        raise SQLAlchemyTimeoutError("QueuePool limit reached")

    with TestClient(test_app) as client:
        response = client.get("/pool-timeout")

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert response.json()["error"]["code"] == "DB_BUSY"


def test_wrapped_connection_pool_timeout_is_detected():
    class WrappedError(Exception):
        def __init__(self, *exceptions):
            super().__init__("request failed")
            self.exceptions = exceptions

    wrapped = WrappedError(SQLAlchemyTimeoutError("QueuePool limit reached"))
    assert _contains_pool_timeout(wrapped) is True


def test_large_static_assets_are_gzipped():
    with TestClient(app) as client:
        response = client.get(
            "/static/css/style.css",
            headers={"Accept-Encoding": "gzip"},
        )

    assert response.status_code == 200
    assert response.headers.get("content-encoding") == "gzip"
    assert len(response.content) > 1024


def test_pool_settings_are_documented_in_env_example():
    env_example = (Path(__file__).resolve().parents[1] / ".env.example").read_text(
        encoding="utf-8"
    )
    for key in (
        "DB_POOL_SIZE=20",
        "DB_MAX_OVERFLOW=20",
        "DB_POOL_TIMEOUT_SECONDS=10",
        "DB_POOL_RECYCLE_SECONDS=1800",
    ):
        assert key in env_example
def test_binary_responses_are_not_gzipped():
    test_app = FastAPI()
    test_app.add_middleware(SelectiveGZipMiddleware, minimum_size=128)

    @test_app.get("/file.pdf")
    def pdf_file():
        return Response(content=b"%PDF" + (b"x" * 2048), media_type="application/pdf")

    with TestClient(test_app) as client:
        response = client.get(
            "/file.pdf",
            headers={"Accept-Encoding": "gzip"},
        )

    assert response.status_code == 200
    assert "content-encoding" not in response.headers
    assert response.content.startswith(b"%PDF")
def test_gzip_accept_encoding_parser_is_not_naive():
    from app.core.compression import _accepts_gzip

    assert _accepts_gzip("gzip") is True
    assert _accepts_gzip("gzip;q=0") is False
    assert _accepts_gzip("xgzip") is False
    assert _accepts_gzip("GZIP;q=1") is True


def test_sqlite_busy_timeout_is_configured_before_journal_mode():
    source = (Path(__file__).resolve().parents[1] / "app/db/session.py").read_text(encoding="utf-8")
    busy_idx = source.index('cursor.execute("PRAGMA busy_timeout=5000")')
    journal_idx = source.index('cursor.execute("PRAGMA journal_mode=WAL")')
    assert busy_idx < journal_idx
