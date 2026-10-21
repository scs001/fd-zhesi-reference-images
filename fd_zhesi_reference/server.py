"""哲思参照系 FastMCP 服务器。

骨架阶段：工具以诚实错误回报未接线状态（指名 tracking task）。
HTTP 面挂 /mcp（MCP streamable-http），/healthz 供探活。
工具面按源分模块（tools_atlas / tools_sep / tools_survey / tools_registry），
Wikidata 对齐结果并入 philosophers 表（tools_wikidata 不独立挂工具）。
"""

from __future__ import annotations

from fastmcp import FastMCP

from . import tools_atlas, tools_registry, tools_sep, tools_survey
from .config import Settings

SERVER_NAME = "fd-zhesi-reference"

INSTRUCTIONS = (
    "哲思参照系：哲学概念（SEP 引用层）、人物与六轴坐标、关系边（含极性）、"
    "立场分布（PhilPapers Survey）。所有结果携带 provenance"
    "（source/stable_id/as_of/attribution，Atlas 为 CC BY 4.0 需署名）；"
    "未命中如实返回空结果并带原因，绝不编造。骨架期工具以诚实错误回报未接线状态。"
)


def create_server(settings: Settings | None = None) -> FastMCP:
    settings = settings or Settings.from_env()
    mcp: FastMCP = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)
    tools_atlas.register(mcp, settings)
    tools_sep.register(mcp, settings)
    tools_survey.register(mcp, settings)
    tools_registry.register(mcp, settings)
    return mcp
