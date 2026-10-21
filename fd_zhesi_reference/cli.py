"""CLI：serve（HTTP 面 = /mcp + /healthz）。"""

from __future__ import annotations

import asyncio

import click
import uvicorn

from . import __version__
from .server import SERVER_NAME, create_server


@click.group()
@click.version_option(__version__)
def cli() -> None:
    """fd-zhesi-reference：哲思参照系 MCP。"""


@cli.command()
@click.option("--host", default="0.0.0.0", show_default=True)
@click.option("--port", default=8000, show_default=True)
def serve(host: str, port: int) -> None:
    """启动 HTTP 服务（/mcp + /healthz）。"""
    mcp = create_server()
    app = mcp.http_app(path="/mcp")

    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse(
            {"status": "ok", "service": SERVER_NAME, "version": __version__}
        )

    app.routes.append(Route("/healthz", healthz))

    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    asyncio.run(uvicorn.Server(config).serve())


if __name__ == "__main__":
    cli()
