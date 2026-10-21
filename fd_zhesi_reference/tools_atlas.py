"""Atlas 种子图谱工具（task 2.2 已接线）。数据 CC BY 4.0，attribution 随行。

契约（spec：参照系查询 / 缺口与失败口径）：
- philosopher_query：按 pt/en/zh 名字不区分大小写包含匹配，命中返回三语名、
  生卒、学派（三语）、地区（三语）、六轴坐标、影响力分、wikidata_qid，
  每条结果带 provenance（含 CC BY 4.0 署名）。
- relation_query：两人名解析为 atlas_id（复用上面的匹配规则），返回两人之间的
  有向影响边列表——极性（positive 赞同 / critical 批判 / synthetic 综合）、
  权重与方向（forward=按查询方向，reverse=图中反向边），附两端人物摘要。
- 空结果必带原因，绝不静默为空：
    not_indexed     查无此人 / 两人均已收录但无关系边
    not_ingested    表已建但图谱未摄取（先跑 scripts/ingest_atlas.py）
    unknown_entity  relation_query 某端人名解析不到（hint 给出是谁）
- 参照库从未初始化（连 philosophers 表都没有，如 :memory: 骨架库）时，
  按骨架期诚实错误回报（HonestError），不编造数据。
"""

from __future__ import annotations

import sqlite3

from fastmcp import FastMCP

from .config import Settings
from .errors import HonestError
from .provenance import provenance

SOURCE = "philosophy-atlas"
AXES = (
    "individualism",
    "materialism",
    "rationalism",
    "determinism",
    "transcendence",
    "universalism",
)
NAME_COLUMNS = ("name_pt", "name_en", "name_zh")


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def _count(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COUNT(*) FROM philosophers").fetchone()[0]


def _person(row: sqlite3.Row) -> dict:
    return {
        "atlas_id": row["atlas_id"],
        "name": {"pt": row["name_pt"], "en": row["name_en"], "zh": row["name_zh"]},
        "birth_year": row["birth_year"],
        "death_year": row["death_year"],
        "school": {"pt": row["school"], "en": row["school_en"], "zh": row["school_zh"]},
        "region": {"pt": row["region"], "en": row["region_en"], "zh": row["region_zh"]},
        "axes": {axis: row[axis] for axis in AXES},
        "influence_score": row["influence_score"],
        "wikidata_qid": row["wikidata_qid"],
        "provenance": provenance(SOURCE, f"atlas:{row['atlas_id']}", row["ingested_at"]),
    }


def _match_people(conn: sqlite3.Connection, name: str) -> list[sqlite3.Row]:
    """三语名字包含匹配。用 Python casefold 而非 SQL lower——SQLite 的
    lower() 只处理 ASCII，匹配不了 'Sócrates' 这类带音符的名字。"""
    needle = name.strip().casefold()
    if not needle:
        return []
    hits = [
        row
        for row in conn.execute("SELECT * FROM philosophers")
        if any(needle in (row[col] or "").casefold() for col in NAME_COLUMNS)
    ]
    hits.sort(key=lambda r: (-(r["influence_score"] or 0.0), r["atlas_id"]))
    return hits


def query_philosopher(settings: Settings, name: str) -> dict:
    """philosopher_query 的实现（register 时绑定 settings 闭包）。"""
    conn = settings.connect()
    try:
        if not _table_exists(conn, "philosophers"):
            raise HonestError("philosopher_query", "2.2")
        total = _count(conn)
        if total == 0:
            return {
                "results": [],
                "reason": "not_ingested",
                "hint": "Atlas 种子图谱尚未摄取（运行 scripts/ingest_atlas.py 后可查）",
            }
        hits = _match_people(conn, name)
        if not hits:
            return {
                "results": [],
                "reason": "not_indexed",
                "hint": f"Atlas 图谱（{total} 人）中未找到与 {name!r} 匹配的哲学家",
            }
        return {"results": [_person(row) for row in hits]}
    finally:
        conn.close()


def query_relation(settings: Settings, source: str, target: str) -> dict:
    """relation_query 的实现（register 时绑定 settings 闭包）。"""
    conn = settings.connect()
    try:
        if not _table_exists(conn, "philosophers"):
            raise HonestError("relation_query", "2.2")
        total = _count(conn)
        if total == 0:
            return {
                "results": [],
                "reason": "not_ingested",
                "hint": "Atlas 种子图谱尚未摄取（运行 scripts/ingest_atlas.py 后可查）",
            }
        src_rows = _match_people(conn, source)
        if not src_rows:
            return {
                "results": [],
                "reason": "unknown_entity",
                "hint": f"无法解析 source={source!r}：Atlas 图谱（{total} 人）中无匹配",
            }
        tgt_rows = _match_people(conn, target)
        if not tgt_rows:
            return {
                "results": [],
                "reason": "unknown_entity",
                "hint": f"无法解析 target={target!r}：Atlas 图谱（{total} 人）中无匹配",
            }

        edges: list[dict] = []
        for src in src_rows:
            for tgt in tgt_rows:
                if src["atlas_id"] == tgt["atlas_id"]:
                    continue
                for origin, dest, direction in (
                    (src, tgt, "forward"),
                    (tgt, src, "reverse"),
                ):
                    for edge in conn.execute(
                        "SELECT * FROM relations WHERE source_id=? AND target_id=?",
                        (origin["atlas_id"], dest["atlas_id"]),
                    ):
                        edges.append(
                            {
                                "from": origin["atlas_id"],
                                "to": dest["atlas_id"],
                                "from_name": origin["name_pt"],
                                "to_name": dest["name_pt"],
                                "direction": direction,
                                "polarity": edge["polarity"],
                                "weight": edge["weight"],
                                "provenance": provenance(
                                    SOURCE,
                                    f"atlas:{origin['atlas_id']}->{dest['atlas_id']}",
                                    edge["ingested_at"],
                                ),
                            }
                        )
        if not edges:
            return {
                "results": [],
                "reason": "not_indexed",
                "hint": "两位人物均已收录，但 Atlas 图谱未记录两人之间的影响边",
                "source": [_person(row) for row in src_rows],
                "target": [_person(row) for row in tgt_rows],
            }
        edges.sort(key=lambda e: (e["direction"] != "forward", -e["weight"]))
        return {
            "results": edges,
            "source": [_person(row) for row in src_rows],
            "target": [_person(row) for row in tgt_rows],
        }
    finally:
        conn.close()


def register(mcp: FastMCP, settings: Settings) -> None:
    """挂工具：闭包绑定 settings（每个 server 实例各自的 DB）。"""

    async def philosopher_query(name: str) -> dict:
        """人物查询：按葡/英/中文名不区分大小写包含匹配，返回命中人物的生卒、
        学派（三语）、地区（三语）、六轴坐标（individualism/materialism/
        rationalism/determinism/transcendence/universalism）、影响力分与
        wikidata_qid；每条结果带 provenance（Atlas 数据 CC BY 4.0，署名随行）。
        空结果带 reason：not_indexed（未收录）/ not_ingested（图谱未摄取）。"""

        return query_philosopher(settings, name)

    async def relation_query(source: str, target: str) -> dict:
        """关系查询：解析两个哲学家人名（匹配规则同 philosopher_query），返回
        两人之间的影响边列表——极性 polarity（positive 赞同 / critical 批判 /
        synthetic 综合）、权重 weight、方向 direction（forward=按查询方向，
        reverse=图中反向边），附两端人物摘要与 provenance（CC BY 4.0 署名随行）。
        空结果带 reason：unknown_entity（人名解析不到，hint 指明是哪端）/
        not_indexed（两人均收录但无关系边）/ not_ingested。"""

        return query_relation(settings, source, target)

    mcp.tool(philosopher_query)
    mcp.tool(relation_query)
