"""PhilPapers Survey 立场分布摄取（task 2.5）。

数据来源（task 1.2 侦察产物，均在 data/raw/philpapers/）：
- survey_parsed.json      2009 轮（Wayback 快照 20230424171640，N=931，初步波口径）
- survey_parsed_2020.json 2020 轮（PhilBench 镜像内嵌官方分布，N=1785）

口径注记（docs/survey-probe.md）：2009 快照是初步波口径，与 2013 论文终版（加权）
存在已知差异（中位 3.7pp）——tabulation 列标记口径，查询面随结果下发。
生产直取通道（爬虫平台 proxy，2020 在 survey2020.philpeople.org 域）属 2.5 后续增强，
本脚本以快照/镜像产物为权威输入，幂等可重跑。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from fd_zhesi_reference.config import Settings  # noqa: E402
from fd_zhesi_reference.schema import init  # noqa: E402

DATA_DIR = REPO / "data" / "raw" / "philpapers"

# 高频选项英→中通俗释义（best-effort；未命中保持 NULL，不做机翻硬造）
GLOSS = {
    "compatibilism": "相容论（自由意志与决定论可以并存）",
    "libertarianism": "自由意志论（人有非决定论的自由）",
    "no free will": "无自由意志（决定论成立）",
    "other": "其他",
    "accept": "接受",
    "reject": "拒绝",
    "accept: some version": "接受（某种版本）",
    "theism": "有神论",
    "atheism": "无神论",
    "agnosticism/other": "不可知论/其他",
    "empiricism": "经验论（知识源于经验）",
    "rationalism": "唯理论（存在先验知识）",
    "physicalism": "物理主义（心灵可完全归约为物理）",
    "non-physicalism": "非物理主义",
    "dualism": "二元论",
    "idealism": "唯心论",
    "materialism": "唯物论",
    "yes": "是",
    "no": "否",
    "knowledge": "知识",
    "justified, true belief + X": "得到辩护的真信念 + 附加条件",
    "correspondence": "符合论（真理即与事实符合）",
    "deflationary": "紧缩论（真理无实质本性）",
    "cognitivism": "认知主义（道德判断可真可假）",
    "non-cognitivism": "非认知主义（道德判断不表信念）",
    "moral realism": "道德实在论",
    "moral anti-realism": "道德反实在论",
    "objective": "客观的",
    "subjective": "主观的",
    "identical": "同一的",
    "not identical": "不同一的",
    "deontology": "义务论",
    "consequentialism": "后果论",
    "virtue ethics": "德性论",
    "analytic-synthetic distinction": "分析-综合区分",
    "scientific realism": "科学实在论",
    "scientific anti-realism": "科学反实在论",
    "teleology": "目的论",
    "naturalism": "自然主义",
    "non-naturalism": "非自然主义",
    "equal": "相等",
    "unequal": "不相等",
    "biological/psychological nature": "生物/心理本性",
    "social construction": "社会建构",
    "invariant": "不变的（绝对主义）",
    "variant": "可变的（相对主义）",
    "fresh": "洛克式（记忆连续性）",
    "brain-based": "大脑基线（物理连续性）",
    "empty": "虚无（无进一步事实）",
    "ordered": "有序（可完全排序）",
    "unordered": "无序",
}

# 各轮受访者基数据（查询面随结果下发，口径见 docs/survey-probe.md）
RESPONDENTS = {"2009": 931, "2020": 1785}


def ensure_columns(conn: sqlite3.Connection) -> None:
    """旧库（摄取前的 DDL）补列，幂等。"""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(stances)")}
    for col, ddl in [
        ("category", "TEXT"),
        ("multi_select", "INTEGER"),
        ("tabulation", "TEXT"),
        ("respondents", "INTEGER"),
    ]:
        if col not in cols:
            conn.execute(f"ALTER TABLE stances ADD COLUMN {col} {ddl}")
    conn.commit()


def ingest(settings: Settings, data_dir: Path) -> dict:
    conn = settings.connect()
    try:
        init(conn)
        ensure_columns(conn)
        today = date.today().isoformat()
        counts = {}
        conn.execute("DELETE FROM stances")
        for fname, tabulation in [
            ("survey_parsed.json", "preliminary-wave (snapshot 20230424171640)"),
            ("survey_parsed_2020.json", "official-2020 (PhilBench mirror)"),
        ]:
            doc = json.loads((data_dir / fname).read_text(encoding="utf-8"))
            rnd = doc["round"]
            n_resp = doc.get("respondents") or RESPONDENTS.get(rnd)
            rows = 0
            for q in doc["questions"]:
                for opt in q["options"]:
                    conn.execute(
                        "INSERT INTO stances (survey_round, question, question_zh, option,"
                        " option_zh, pct, retrieved_at, category, multi_select, tabulation, respondents)"
                        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            rnd,
                            q["question"],
                            None,
                            opt["label"],
                            GLOSS.get(opt["label"].strip().lower()),
                            opt.get("pct"),
                            today,
                            q.get("category") or q.get("topic"),
                            1 if q.get("multi_select") else 0,
                            tabulation,
                            n_resp,
                        ),
                    )
                    rows += 1
            counts[f"{rnd}"] = rows
        conn.execute(
            "INSERT OR REPLACE INTO ingest_log (source, status, detail, as_of) VALUES (?,?,?,?)",
            ("philpapers-survey", "ok", f"{counts} (快照/镜像产物，口径带 tabulation 标记)", today),
        )
        conn.commit()
        return counts
    finally:
        conn.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="PhilPapers Survey 摄取")
    ap.add_argument("--data-dir", default=str(DATA_DIR))
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    settings = Settings.from_env()
    if args.db:
        settings.db_path = args.db
    print(ingest(settings, Path(args.data_dir)))


if __name__ == "__main__":
    main()
