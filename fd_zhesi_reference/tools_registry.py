"""摄取台账工具（task 2.6）：源状态与缺口，未摄取必带原因。"""

from __future__ import annotations

from fastmcp import FastMCP

from .config import Settings
from .schema import init

# 四源基线：未摄取时按此给出缺口原因（spec：缺口可见并注明原因）
SOURCES = {
    "philosophy-atlas": "种子图谱（376 人×六轴+极性关系边），摄取脚本 scripts/ingest_atlas.py（task 2.2）",
    "wikidata": "全量层对齐（QID/name_en/name_zh 回填），scripts/ingest_wikidata.py --apply（task 2.3）",
    "sep": "概念引用层（on-demand 缓存），concept_lookup 调用时自动建立（task 2.4）",
    "philpapers-survey": "立场分布（2009/2020），摄取走爬虫平台 proxy 或 Wayback 快照（task 1.2/2.5）",
}


async def source_status() -> dict:
    """源状态：各外部源的摄取状态与缺口（未摄取必带原因）。"""
    settings = Settings.from_env()
    conn = settings.connect()
    try:
        init(conn)
        rows = conn.execute(
            "SELECT source, status, detail, as_of FROM ingest_log "
            "WHERE as_of = (SELECT MAX(as_of) FROM ingest_log l2 WHERE l2.source = ingest_log.source)"
        ).fetchall()
        sep_cached = conn.execute("SELECT COUNT(*) AS c FROM concepts").fetchone()["c"]
    finally:
        conn.close()

    by_source = {r["source"]: r for r in rows}
    results = []
    for source, expectation in SOURCES.items():
        if source == "sep":
            # SEP 是按需缓存层（无批量摄取）：状态由 concepts 缓存行数决定
            results.append(
                {
                    "source": source,
                    "status": "ok" if sep_cached else "not_ingested",
                    "detail": f"on-demand 缓存 {sep_cached} 条" if sep_cached else None,
                    "reason": None if sep_cached else "not_ingested",
                    "expectation": expectation,
                }
            )
            continue
        row = by_source.get(source)
        if row is None:
            results.append(
                {
                    "source": source,
                    "status": "not_ingested",
                    "reason": "not_ingested",
                    "expectation": expectation,
                }
            )
        else:
            entry = {
                "source": source,
                "status": row["status"],
                "as_of": row["as_of"],
                "expectation": expectation,
            }
            if row["status"] != "ok":
                entry["reason"] = row["detail"] or row["status"]
            results.append(entry)

    return {
        "results": results,
        "provenance": {
            "source": "fd-zhesi-reference",
            "stable_id": "ingest-log",
            "as_of": max((r["as_of"] for r in rows), default=None),
        },
    }


def register(mcp: FastMCP, settings: Settings) -> None:
    mcp.tool(source_status)
