"""SEP 引用层测试（task 2.4，全离线：真实结果页 HTML fixture + 临时 SQLite + monkeypatch）。

许可红线：只断言 标题/摘要片段/entries 链接，任何用例都不得抓词条正文页。
"""

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from fastmcp import FastMCP

from fd_zhesi_reference import schema, tools_sep
from fd_zhesi_reference.config import Settings
from fd_zhesi_reference.sep import (
    SepUnreachable,
    SepUpstream,
    parse_search_results,
    search_url,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sep_sample.html"  # 真实搜索结果页快照


@pytest.fixture
def sample_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(db_path=str(tmp_path / "ref.db"))


@pytest.fixture
def wired(settings, monkeypatch) -> Settings:
    """把 tools_sep 指到临时库（不动全局其他测试的 server 状态）。"""
    monkeypatch.setattr(tools_sep, "_settings", settings)
    return settings


def _fake_fetch(html: str):
    async def fake(term: str, base_url: str = "", timeout: float = 0) -> str:
        return html

    return fake


# ---------------------------------------------------------------- 解析（纯函数）


def test_search_url() -> None:
    assert search_url("free will") == (
        "https://plato.stanford.edu/search/searcher.py?query=free will"
    )


def test_parse_real_fixture(sample_html) -> None:
    hits = parse_search_results(sample_html, limit=5)
    assert 1 <= len(hits) <= 5
    first = hits[0]
    assert first.title == "Compatibilism"
    assert first.url == "https://plato.stanford.edu/entries/compatibilism/"
    for h in hits:
        assert h.url.startswith("https://plato.stanford.edu/entries/")
        assert h.url.endswith("/")
        assert h.title
        assert "<" not in h.snippet  # 标签已剥
        assert "<!--" not in h.snippet
        assert "DefaultSimilarity" not in h.snippet  # lucene 评分注释已剥
    assert len({h.url for h in hits}) == len(hits)  # 去重


def test_parse_respects_limit_and_order(sample_html) -> None:
    three = parse_search_results(sample_html, limit=3)
    five = parse_search_results(sample_html, limit=5)
    assert len(three) == 3
    assert [h.url for h in three] == [h.url for h in five[:3]]  # 排名序


def test_parse_no_entries_links() -> None:
    page = (
        "<html><body><div class='search_results'><div class='search_total'>"
        "0 documents found</div></div></body></html>"
    )
    assert parse_search_results(page) == []


def test_parse_non_entry_results_skipped() -> None:
    """结果里的非 entries 链接（如站内页面）不收录；有 entries 的保留。"""
    page = """
    <div class="result_listing">
      <div class="result_title"><a href="https://plato.stanford.edu/blog/x">Blog Post</a></div>
      <div class="result_snippet">not an entry</div>
      <div class="result_url"><a href="/blog/x">https://plato.stanford.edu/blog/x</a></div>
    </div>
    <div class="result_listing">
      <div class="result_title"><a href="entries/daoism/">Daoism</a></div>
      <div class="result_snippet">Chinese philosophical tradition</div>
      <div class="result_url"><a href="entries/daoism/">entries/daoism/</a></div>
    </div>
    """
    hits = parse_search_results(page)
    assert len(hits) == 1
    assert hits[0].url == "https://plato.stanford.edu/entries/daoism/"
    assert hits[0].title == "Daoism"


def test_parse_redirect_param_fallback() -> None:
    """无 result_url 区时，从 result_title 跳转参数 entry=/entries/<slug>/ 还原链接。"""
    page = """
    <div class="result_listing">
      <div class="result_title"><a class=l
        href="https://plato.stanford.edu/search/r?entry=%2Fentries%2Fqualia%2F&page=1&rank=0">
        <b>Qualia</b></a></div>
      <div class="result_snippet">The <b>qualia</b> debate…</div>
    </div>
    """
    hits = parse_search_results(page)
    assert len(hits) == 1
    assert hits[0].url == "https://plato.stanford.edu/entries/qualia/"
    assert hits[0].title == "Qualia"
    assert "qualia" in hits[0].snippet and "<b>" not in hits[0].snippet


# ---------------------------------------------------------------- concept_lookup（离线）


async def test_lookup_fetches_parses_and_caches(wired, sample_html, monkeypatch) -> None:
    monkeypatch.setattr(tools_sep, "fetch_search_html", _fake_fetch(sample_html))
    out = await tools_sep.concept_lookup("compatibilism")

    assert set(out) == {"results", "provenance"}
    assert len(out["results"]) == 5
    prov = out["provenance"]
    assert prov["source"] == "sep"
    assert prov["stable_id"] == "https://plato.stanford.edu/entries/compatibilism/"
    assert prov["attribution"].startswith("Stanford Encyclopedia of Philosophy")
    assert datetime.fromisoformat(prov["as_of"])  # retrieved_at 可解析

    conn = wired.connect()
    try:
        rows = conn.execute(
            "SELECT title, url, retrieved_at FROM concepts WHERE term = ? ORDER BY rowid",
            ("compatibilism",),
        ).fetchall()
        assert len(rows) == 5
        assert rows[0]["url"] == prov["stable_id"]
        assert {r["retrieved_at"] for r in rows} == {prov["as_of"]}  # 同批同时间戳
    finally:
        conn.close()


async def test_lookup_cache_hit_skips_network(wired, sample_html, monkeypatch) -> None:
    monkeypatch.setattr(tools_sep, "fetch_search_html", _fake_fetch(sample_html))
    first = await tools_sep.concept_lookup("free will")
    assert len(first["results"]) >= 1

    async def must_not_fetch(*a, **kw):
        raise AssertionError("缓存命中时不得再打网络")

    monkeypatch.setattr(tools_sep, "fetch_search_html", must_not_fetch)
    second = await tools_sep.concept_lookup("free will")
    assert second["results"] == first["results"]
    assert second["provenance"] == first["provenance"]  # as_of=retrieved_at


async def test_lookup_stale_cache_refetches(wired, sample_html, monkeypatch) -> None:
    stale = (datetime.now(timezone.utc) - timedelta(days=31)).isoformat()
    conn = wired.connect()
    schema.init(conn)
    conn.execute(
        "INSERT INTO concepts (term, title, url, snippet, retrieved_at)"
        " VALUES ('epistemology', 'Old', 'https://plato.stanford.edu/entries/old/', 'x', ?)",
        (stale,),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(tools_sep, "fetch_search_html", _fake_fetch(sample_html))
    out = await tools_sep.concept_lookup("epistemology")
    urls = [r["url"] for r in out["results"]]
    assert urls and "https://plato.stanford.edu/entries/old/" not in urls  # 不回吐陈旧行
    conn = wired.connect()
    try:
        kept = conn.execute(
            "SELECT COUNT(*) AS n FROM concepts WHERE term = ? AND retrieved_at = ?",
            ("epistemology", stale),
        ).fetchone()["n"]
        assert kept == 0  # 整词覆写
    finally:
        conn.close()


async def test_lookup_unreachable_on_httpx_timeout(wired, monkeypatch) -> None:
    """monkeypatch httpx 抛超时 → reason=unreachable，不抛裸异常。"""

    async def timeout_get(self, *a, **kw):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx.AsyncClient, "get", timeout_get)
    out = await tools_sep.concept_lookup("determinism")
    assert out == {"results": [], "reason": "unreachable"}


async def test_lookup_unreachable_wraps_error(wired, monkeypatch) -> None:
    async def boom(*a, **kw):
        raise SepUnreachable("dns failure")

    monkeypatch.setattr(tools_sep, "fetch_search_html", boom)
    assert await tools_sep.concept_lookup("qualia") == {"results": [], "reason": "unreachable"}


async def test_lookup_not_indexed_when_no_entries(wired, monkeypatch) -> None:
    monkeypatch.setattr(
        tools_sep,
        "fetch_search_html",
        _fake_fetch("<html><body>nothing here</body></html>"),
    )
    assert await tools_sep.concept_lookup("zzznonsense") == {
        "results": [],
        "reason": "not_indexed",
    }


async def test_lookup_upstream_on_non_200(wired, monkeypatch) -> None:
    async def boom(*a, **kw):
        raise SepUpstream("HTTP 503")

    monkeypatch.setattr(tools_sep, "fetch_search_html", boom)
    assert await tools_sep.concept_lookup("idealism") == {"results": [], "reason": "upstream"}


async def test_lookup_blank_term(wired, monkeypatch) -> None:
    async def must_not_fetch(*a, **kw):
        raise AssertionError("空词不得打网络")

    monkeypatch.setattr(tools_sep, "fetch_search_html", must_not_fetch)
    assert await tools_sep.concept_lookup("   ") == {"results": [], "reason": "not_indexed"}


async def test_lookup_creates_schema_idempotently(wired, sample_html, monkeypatch) -> None:
    """首次调用在空库上自动建表（schema.init 幂等），可重复调用。"""
    monkeypatch.setattr(tools_sep, "fetch_search_html", _fake_fetch(sample_html))
    for _ in range(2):
        out = await tools_sep.concept_lookup("stoicism")
        assert len(out["results"]) >= 1


async def test_lookup_via_mcp_tool(wired, sample_html, monkeypatch) -> None:
    """经 FastMCP 注册后的工具签名可正常调用（term: str）。"""
    monkeypatch.setattr(tools_sep, "fetch_search_html", _fake_fetch(sample_html))
    mcp = FastMCP("t")
    tools_sep.register(mcp, wired)
    tools = {t.name: t for t in await mcp.list_tools()}
    assert "concept_lookup" in tools
    out = await tools["concept_lookup"].fn(term="pragmatism")
    assert out["provenance"]["source"] == "sep"


async def test_concurrent_lookup_safe_for_sqlite(wired, sample_html, monkeypatch) -> None:
    """并发两次不同词查询（SQLite 各自短连接）不炸——WAL + 及时关闭。"""
    monkeypatch.setattr(tools_sep, "fetch_search_html", _fake_fetch(sample_html))
    await asyncio.gather(
        tools_sep.concept_lookup("dualism"),
        tools_sep.concept_lookup("realism"),
    )
    conn = sqlite3.connect(wired.db_path)
    try:
        terms = {r[0] for r in conn.execute("SELECT DISTINCT term FROM concepts")}
        assert terms == {"dualism", "realism"}
    finally:
        conn.close()
