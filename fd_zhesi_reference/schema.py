"""参照系库 DDL（SQLite，可重建缓存；design D3/D7）。

中西无感：school/region 为开放词表（pt 原值 + en/zh 映射列），不绑西哲枚举。
"""

from __future__ import annotations

import sqlite3

DDL = [
    # Atlas 种子图谱（task 2.2）
    """CREATE TABLE IF NOT EXISTS philosophers (
        atlas_id INTEGER PRIMARY KEY,
        name_pt TEXT NOT NULL,
        name_en TEXT,
        name_zh TEXT,
        birth_year INTEGER,
        death_year INTEGER,
        school TEXT, school_en TEXT, school_zh TEXT,
        region TEXT, region_en TEXT, region_zh TEXT,
        individualism REAL, materialism REAL, rationalism REAL,
        determinism REAL, transcendence REAL, universalism REAL,
        influence_score REAL,
        wikidata_qid TEXT,
        ingested_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS relations (
        atlas_id INTEGER PRIMARY KEY AUTOINCREMENT,
        source_id INTEGER NOT NULL REFERENCES philosophers(atlas_id),
        target_id INTEGER NOT NULL REFERENCES philosophers(atlas_id),
        weight REAL NOT NULL,
        polarity TEXT NOT NULL,  -- positive / critical / synthetic
        ingested_at TEXT NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS topics (
        topic_id TEXT PRIMARY KEY,
        slug TEXT, label_pt TEXT, label_en TEXT, label_zh TEXT,
        category TEXT,
        description TEXT,
        key_philosophers TEXT,  -- JSON array of atlas_id
        ingested_at TEXT NOT NULL
    )""",
    # SEP 引用层缓存（task 2.4；只存标题+摘要+链接，不存正文）
    """CREATE TABLE IF NOT EXISTS concepts (
        term TEXT NOT NULL,
        title TEXT NOT NULL,
        url TEXT NOT NULL,
        snippet TEXT,
        retrieved_at TEXT NOT NULL,
        PRIMARY KEY (term, url)
    )""",
    # PhilPapers Survey 立场分布（task 2.5）
    """CREATE TABLE IF NOT EXISTS stances (
        survey_round TEXT NOT NULL,  -- 2009 / 2020
        question TEXT NOT NULL,
        question_zh TEXT,
        option TEXT NOT NULL,
        option_zh TEXT,
        pct REAL,
        retrieved_at TEXT NOT NULL,
        PRIMARY KEY (survey_round, question, option)
    )""",
    # 摄取台账（task 2.6 source_status 的数据面）
    """CREATE TABLE IF NOT EXISTS ingest_log (
        source TEXT NOT NULL,
        status TEXT NOT NULL,     -- ok / partial / failed / not_ingested
        detail TEXT,
        as_of TEXT NOT NULL,
        PRIMARY KEY (source, as_of)
    )""",
]

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_philosophers_names ON philosophers(name_en, name_zh)",
    "CREATE INDEX IF NOT EXISTS idx_relations_src ON relations(source_id)",
    "CREATE INDEX IF NOT EXISTS idx_relations_dst ON relations(target_id)",
    "CREATE INDEX IF NOT EXISTS idx_stances_q ON stances(question)",
]


def init(conn: sqlite3.Connection) -> None:
    for ddl in DDL:
        conn.execute(ddl)
    for idx in INDEXES:
        conn.execute(idx)
    conn.commit()
