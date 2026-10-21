"""SEP 引用层：HTTP 抓取与 HTML 解析（纯函数，task 2.4）。

许可口径（openspec change zhesi-worldview-workshop，design D2）：
SEP 只链不抄——本层仅从搜索结果页提取 标题 / 摘要片段 / entries URL，
绝不去抓词条正文页。摘要片段是搜索结果页自带的引用性片段（通常为条目首段截断）。

解析只用标准库（re/html/urllib），不引入 bs4 等未声明依赖。
"""

from __future__ import annotations

import html as html_lib
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

import httpx

USER_AGENT = "fd-zhesi-reference/0.1"
DEFAULT_TIMEOUT = 8.0  # spec 失败口径：超时 8s → unreachable
SEARCH_PATH = "/search/searcher.py"
DEFAULT_BASE = "https://plato.stanford.edu"


class SepUnreachable(Exception):
    """SEP 不可达（网络层：DNS/连接/超时/TLS）。映射 reason=unreachable。"""


class SepUpstream(Exception):
    """SEP 可达但响应异常（非 200）。映射 reason=upstream。"""


@dataclass(frozen=True)
class SepHit:
    """一条引用性结果：标题 + 摘要片段 + entries 链接。不存正文。"""

    title: str
    url: str
    snippet: str

    def as_dict(self) -> dict:
        return {"title": self.title, "url": self.url, "snippet": self.snippet}


def search_url(term: str, base_url: str = DEFAULT_BASE) -> str:
    """SEP 搜索端点 URL（GET，返回 HTML 结果页）。"""
    return f"{base_url.rstrip('/')}{SEARCH_PATH}?query={term}"


async def fetch_search_html(
    term: str,
    base_url: str = DEFAULT_BASE,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    """实时抓取搜索结果页 HTML。网络层失败抛 SepUnreachable，非 200 抛 SepUpstream。"""
    url = f"{base_url.rstrip('/')}{SEARCH_PATH}"
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            resp = await client.get(url, params={"query": term})
    except httpx.HTTPError as exc:  # 含 TimeoutException / TransportError
        raise SepUnreachable(f"{type(exc).__name__}: {exc}") from exc
    if resp.status_code != 200:
        raise SepUpstream(f"HTTP {resp.status_code} for query={term!r}")
    return resp.text


# ---------------------------------------------------------------- 解析（纯函数）

_BLOCK_SPLIT_RE = re.compile(r'<div class="result_listing">')
_TITLE_RE = re.compile(r'<div class="result_title">\s*<a\b[^>]*>(.*?)</a>', re.S)
_SNIPPET_RE = re.compile(r'<div class="result_snippet">(.*?)</div>', re.S)
_RESULT_URL_RE = re.compile(r'<div class="result_url">\s*<a\b[^>]*>(.*?)</a>', re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_ENTRY_SLUG_RE = re.compile(r"entries/([^/?#&]+)")
_WS_RE = re.compile(r"\s+")


def _clean_text(raw: str, strip_comments: bool = False) -> str:
    """去注释/标签 → 反转义实体 → 折叠空白。"""
    if strip_comments:
        raw = _COMMENT_RE.sub(" ", raw)
    text = html_lib.unescape(_TAG_RE.sub(" ", raw))
    return _WS_RE.sub(" ", text).strip()


def _entry_url_from_display(raw: str, base_url: str) -> str | None:
    """优先取 result_url 区展示的规范链接文本（…/entries/<slug>/）。"""
    candidate = html_lib.unescape(raw).strip()
    m = _ENTRY_SLUG_RE.search(candidate)
    if not m:
        return None
    return f"{base_url.rstrip('/')}/entries/{m.group(1)}/"


def _entry_url_from_redirect(href: str, base_url: str) -> str | None:
    """兜底：从 result_title 锚点的 search/r?entry=/entries/<slug>/ 跳转参数还原。"""
    try:
        qs = parse_qs(urlparse(html_lib.unescape(href)).query)
        entry = qs.get("entry", [""])[0]
    except ValueError:
        return None
    m = _ENTRY_SLUG_RE.search(unquote(entry))
    if not m:
        return None
    return f"{base_url.rstrip('/')}/entries/{m.group(1)}/"


def parse_search_results(
    html: str, base_url: str = DEFAULT_BASE, limit: int = 5
) -> list[SepHit]:
    """解析搜索结果页 → 前 limit 条含 entries 链接的引用性结果（按排名序，按 URL 去重）。

    无 entries 链接时返回空列表（调用方据此回报 not_indexed）。
    """
    hits: list[SepHit] = []
    seen: set[str] = set()
    chunks = _BLOCK_SPLIT_RE.split(html)[1:]  # 首段是结果列表之前的页面骨架
    for chunk in chunks:
        if len(hits) >= limit:
            break
        title_m = _TITLE_RE.search(chunk)
        if not title_m:
            continue
        url: str | None = None
        url_m = _RESULT_URL_RE.search(chunk)
        if url_m:
            url = _entry_url_from_display(url_m.group(1), base_url)
        if url is None:
            href_m = re.search(r'href="([^"]+)"', title_m.group(0))
            if href_m:
                url = _entry_url_from_redirect(href_m.group(1), base_url)
        if url is None or url in seen:
            continue  # 非 entries 结果（如站内其他页面）不收
        snippet_m = _SNIPPET_RE.search(chunk)
        snippet = _clean_text(snippet_m.group(1), strip_comments=True) if snippet_m else ""
        hits.append(SepHit(title=_clean_text(title_m.group(1)), url=url, snippet=snippet))
        seen.add(url)
    return hits
