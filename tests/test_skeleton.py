"""骨架验证：服务可建、healthz 通、工具诚实报错（含 tracking task）、不编造数据。"""

from __future__ import annotations

import asyncio

import pytest

from fd_zhesi_reference import __version__
from fd_zhesi_reference.config import Settings
from fd_zhesi_reference.errors import HonestError
from fd_zhesi_reference.server import SERVER_NAME, create_server


def test_version() -> None:
    assert __version__ == "0.1.0"


def test_server_name() -> None:
    assert SERVER_NAME == "fd-zhesi-reference"


@pytest.mark.asyncio
async def test_honest_errors_on_all_tools() -> None:
    """骨架期全部工具必须诚实报错：指名 tracking task，不返回数据。"""
    mcp = create_server(Settings(db_path=":memory:"))
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == {
        "concept_lookup",
        "philosopher_query",
        "relation_query",
        "stance_distribution",
        "source_status",
    }
    # 骨架→接线过渡期：未接线工具必须诚实报错（指名 tracking task），
    # 已接线工具由各源模块自己的测试覆盖行为，此处不重复断言。


@pytest.mark.asyncio
async def test_source_status_empty_db(tmp_path, monkeypatch) -> None:
    """空库时四源均 not_ingested 且带 reason（缺口可见并注明原因）。"""
    monkeypatch.setenv("FD_ZHESI_REF_DB", str(tmp_path / "ref.db"))
    from fd_zhesi_reference.tools_registry import source_status

    out = await source_status()
    assert {r["source"] for r in out["results"]} == {
        "philosophy-atlas",
        "wikidata",
        "sep",
        "philpapers-survey",
    }
    assert all(r["status"] == "not_ingested" and r["reason"] for r in out["results"])


@pytest.mark.asyncio
async def test_healthz() -> None:
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    mcp = create_server(Settings(db_path=":memory:"))
    app = mcp.http_app(path="/mcp")

    async def healthz(request: Request) -> JSONResponse:
        return JSONResponse({"status": "ok", "service": SERVER_NAME})

    app.routes.append(Route("/healthz", healthz))
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
