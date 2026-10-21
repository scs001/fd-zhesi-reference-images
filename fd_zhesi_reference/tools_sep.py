"""SEP 引用层工具（task 2.4 接线）。

concept_lookup：概念查询。SEP 许可只链不抄——仅存 标题+摘要片段+entries 链接，
正文不落库、不抓取词条正文页。结果带 provenance；空结果必带原因，绝不编造。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

from fastmcp import FastMCP

from . import schema
from .config import Settings
from .provenance import provenance
from .sep import (
    DEFAULT_TIMEOUT,
    SepHit,
    SepUnreachable,
    SepUpstream,
    fetch_search_html,
    parse_search_results,
)

CACHE_TTL_DAYS = 30  # 缓存命中窗口：retrieved_at 在 30 天内
DEFAULT_LIMIT = 5  # 默认取前 5 条

# register() 时由 server 注入；直接调用（CLI/测试）时回退 from_env()
_settings: Settings | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_fresh(retrieved_at: str, now: datetime) -> bool:
    try:
        ts = datetime.fromisoformat(retrieved_at)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts >= now - timedelta(days=CACHE_TTL_DAYS)


def _read_cache(conn: sqlite3.Connection, term: str) -> tuple[list[SepHit], str]:
    """读 30 天内的缓存行（rowid 序 = 写入时的排名序）。

    返回 (hits, retrieved_at)；无新鲜行时 hits 为空。
    """
    now = datetime.now(timezone.utc)
    rows = conn.execute(
        "SELECT title, url, snippet, retrieved_at FROM concepts WHERE term = ? ORDER BY rowid",
        (term,),
    ).fetchall()
    hits: list[SepHit] = []
    retrieved_at = ""
    for r in rows:  # 同词同批写入共用一个 retrieved_at，取首行即可
        if not _is_fresh(r["retrieved_at"], now):
            continue
        if not retrieved_at:
            retrieved_at = r["retrieved_at"]
        hits.append(SepHit(title=r["title"], url=r["url"], snippet=r["snippet"] or ""))
    return hits, retrieved_at


def _write_cache(conn: sqlite3.Connection, term: str, hits: list[SepHit]) -> str:
    """整词覆写缓存（删旧插新，单事务），返回本次 retrieved_at。"""
    retrieved_at = _now_iso()
    try:
        conn.execute("BEGIN")
        conn.execute("DELETE FROM concepts WHERE term = ?", (term,))
        conn.executemany(
            "INSERT INTO concepts (term, title, url, snippet, retrieved_at)"
            " VALUES (?, ?, ?, ?, ?)",
            [(term, h.title, h.url, h.snippet, retrieved_at) for h in hits],
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return retrieved_at


def _as_of(hits: list[SepHit], retrieved_at: str) -> dict:
    return provenance("sep", stable_id=hits[0].url, as_of=retrieved_at)


async def concept_lookup(term: str) -> dict:
    """概念查询：哲学词语的定义摘要与 SEP 引用链接（只链不抄，正文不落库）。

    命中 30 天内缓存直接返回；未命中实时查 SEP 搜索端点并写缓存。
    空结果必带原因：unreachable（不可达/超时 8s）/ not_indexed（无 entries 结果）/
    upstream（可达但响应异常）。
    """
    settings = _settings or Settings.from_env()
    query = " ".join(term.split())
    if not query:
        return {"results": [], "reason": "not_indexed"}

    conn = settings.connect()
    try:
        schema.init(conn)
        cached, cached_at = _read_cache(conn, query)
        if cached:
            return {
                "results": [h.as_dict() for h in cached],
                "provenance": _as_of(cached, cached_at),
            }
        try:
            html = await fetch_search_html(query, base_url=settings.sep_base, timeout=DEFAULT_TIMEOUT)
        except SepUnreachable:
            return {"results": [], "reason": "unreachable"}
        except SepUpstream:
            return {"results": [], "reason": "upstream"}
        hits = parse_search_results(html, base_url=settings.sep_base, limit=DEFAULT_LIMIT)
        if not hits:
            return {"results": [], "reason": "not_indexed"}
        retrieved_at = _write_cache(conn, query, hits)
        return {"results": [h.as_dict() for h in hits], "provenance": _as_of(hits, retrieved_at)}
    finally:
        conn.close()


def register(mcp: FastMCP, settings: Settings) -> None:
    global _settings
    _settings = settings
    mcp.tool(concept_lookup)
