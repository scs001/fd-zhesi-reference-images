"""PhilPapers Survey 立场分布工具（task 2.5 接线）。

数据由 scripts/ingest_survey.py 从 task 1.2 的快照/镜像产物入库；
口径随行（tabulation + respondents + 年份），2009 为初步波口径（见 docs/survey-probe.md）。
"""

from __future__ import annotations

from fastmcp import FastMCP

from .config import Settings
from .schema import init


async def stance_distribution(question: str) -> dict:
    """立场分布：经典立场问题上职业哲学家的立场分布（带轮次年份与口径注记）。"""
    settings = Settings.from_env()
    conn = settings.connect()
    try:
        init(conn)
        total = conn.execute("SELECT COUNT(*) AS c FROM stances").fetchone()["c"]
        if not total:
            return {"results": [], "reason": "not_ingested"}

        q = question.strip().casefold()
        rows = conn.execute(
            "SELECT survey_round, question, option, option_zh, pct, category,"
            " tabulation, respondents, retrieved_at FROM stances"
            " WHERE lower(question) LIKE ? ORDER BY survey_round, pct DESC",
            (f"%{q}%",),
        ).fetchall()
        if not rows:
            return {"results": [], "reason": "not_indexed"}

        by_round: dict[str, dict] = {}
        for r in rows:
            bucket = by_round.setdefault(
                r["survey_round"],
                {
                    "survey_round": r["survey_round"],
                    "question": r["question"],
                    "category": r["category"],
                    "tabulation": r["tabulation"],
                    "respondents": r["respondents"],
                    "as_of": r["retrieved_at"],
                    "options": [],
                    "provenance": {
                        "source": "philpapers-survey",
                        "stable_id": f"survey-{r['survey_round']}:{r['question']}",
                        "as_of": r["retrieved_at"],
                        "attribution": "PhilPapers Surveys (2009/2020), https://philpapers.org/surveys",
                    },
                },
            )
            bucket["options"].append(
                {
                    "label": r["option"],
                    "label_zh": r["option_zh"],  # 通俗释义，未命中为 null（宁缺毋滥）
                    "pct": r["pct"],
                }
            )
        return {"results": list(by_round.values())}
    finally:
        conn.close()


def register(mcp: FastMCP, settings: Settings) -> None:
    mcp.tool(stance_distribution)
