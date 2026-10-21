"""task 2.5 离线测试：摄取→查询→口径与空结果语义。读仓内快照产物，不联网。"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))

from fd_zhesi_reference.config import Settings  # noqa: E402
from fd_zhesi_reference.tools_survey import stance_distribution  # noqa: E402
from ingest_survey import ingest  # noqa: E402


@pytest.fixture()
def survey_db(tmp_path, monkeypatch):
    db = tmp_path / "survey.db"
    monkeypatch.setenv("FD_ZHESI_REF_DB", str(db))
    counts = ingest(Settings.from_env(), REPO / "data" / "raw" / "philpapers")
    return counts


def test_ingest_counts(survey_db) -> None:
    # 2009: 31 题 104 选项行；2020: 100 题 288 选项行（task 1.2 实测口径）
    assert survey_db["2009"] == 104
    assert survey_db["2020"] == 288


@pytest.mark.asyncio
async def test_free_will_both_rounds(survey_db) -> None:
    out = await stance_distribution("free will")
    rounds = {r["survey_round"] for r in out["results"]}
    assert rounds == {"2009", "2020"}  # 年份标注随行
    for r in out["results"]:
        assert r["respondents"] in (931, 1785)
        assert r["tabulation"]
        assert r["provenance"]["attribution"].startswith("PhilPapers Surveys")
        labels = {o["label"] for o in r["options"]}
        assert "compatibilism" in labels
        comp = next(o for o in r["options"] if o["label"] == "compatibilism")
        assert comp["label_zh"]  # 通俗释义命中
        assert 50 < comp["pct"] < 70  # 两轮相容论均在 ~59%


@pytest.mark.asyncio
async def test_not_indexed(survey_db) -> None:
    out = await stance_distribution("量子引力的道德含义")
    assert out == {"results": [], "reason": "not_indexed"}


@pytest.mark.asyncio
async def test_not_ingested(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("FD_ZHESI_REF_DB", str(tmp_path / "empty.db"))
    out = await stance_distribution("free will")
    assert out == {"results": [], "reason": "not_ingested"}
