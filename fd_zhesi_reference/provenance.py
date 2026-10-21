"""溯源契约：每个结果携带 provenance（source / stable_id / as_of / attribution）。

Atlas 数据为 CC BY 4.0，attribution 必须随结果下发（docs/data-license.md）。
缺失 provenance 即 bug，不做降级——对齐 fd-legal-search-mcp 的 lex-mcp-surface 契约。
"""

from __future__ import annotations

from datetime import date

ATTRIBUTIONS = {
    "philosophy-atlas": "Philosophy Atlas (MBGuedes), CC BY 4.0, https://philosophyatlas.com.br",
    "sep": "Stanford Encyclopedia of Philosophy, https://plato.stanford.edu (link-only citation)",
    "wikidata": "Wikidata (CC0), https://www.wikidata.org",
    "philpapers-survey": "PhilPapers Surveys (2009/2020), https://philpapers.org/surveys",
}


def provenance(source: str, stable_id: str, as_of: str | None = None) -> dict:
    return {
        "source": source,
        "stable_id": stable_id,
        "as_of": as_of or date.today().isoformat(),
        "attribution": ATTRIBUTIONS.get(source, source),
    }
