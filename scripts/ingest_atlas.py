"""Atlas 种子图谱摄取器（task 2.2）。

从 Philosophy Atlas（https://philosophyatlas.com.br，CC BY 4.0 需署名，
见 docs/data-license.md）拉取三个静态数据文件入库：

  data/raw/philosophers.csv            人物（六轴坐标 / 学派 / 地区 / 影响力分）
  data/raw/influences.csv              影响边（positive / critical / synthetic）
  data/catalogue/topics/topics.json    当代议题目录

特性：
- 幂等：单事务内 DELETE 后重插，可安全重跑。注意 philosophers.wikidata_qid
  由 task 2.3 的 Wikidata 对齐任务负责回填，本脚本 INSERT 时恒置 NULL，
  不写该列的其他语义。
- 台账：每次运行在 ingest_log 记一行 source='philosophy-atlas'
  （status ok/partial/failed + 计数明细），供 task 2.6 source_status 读取。
- 离线：--data-dir 指向含 philosophers.csv / influences.csv / topics.json
  的本地缓存目录（测试与断网用），不发起任何 HTTP 请求。

用法：
  python3 scripts/ingest_atlas.py                 # 直连拉取（UA fd-zhesi-reference/0.1）
  python3 scripts/ingest_atlas.py --data-dir DIR  # 本地缓存目录
  python3 scripts/ingest_atlas.py --db PATH       # 覆盖 DB 路径（默认 FD_ZHESI_REF_DB）
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from atlas_labels import (  # noqa: E402  (scripts/ 目录在 sys.path[0])
    FAMOUS_NAMES,
    REGION_EN,
    REGION_ZH,
    SCHOOL_EN,
    SCHOOL_ZH,
)
from fd_zhesi_reference import schema  # noqa: E402
from fd_zhesi_reference.config import Settings  # noqa: E402

SOURCE = "philosophy-atlas"
UA = "fd-zhesi-reference/0.1"
AXES = (
    "individualism",
    "materialism",
    "rationalism",
    "determinism",
    "transcendence",
    "universalism",
)
POLARITIES = {"positive", "critical", "synthetic"}  # 白皮书口径
PATHS = {
    "philosophers": "data/raw/philosophers.csv",
    "influences": "data/raw/influences.csv",
    "topics": "data/catalogue/topics/topics.json",
}

PHILOSOPHER_COLUMNS = (
    ("atlas_id", "name_pt", "name_en", "name_zh", "birth_year", "death_year")
    + ("school", "school_en", "school_zh", "region", "region_en", "region_zh")
    + AXES
    + ("influence_score",)
)


def _int_or_none(raw: str | None) -> int | None:
    if raw is None or not str(raw).strip():
        return None
    return int(float(str(raw).strip()))  # 源文件混有 "-545.0" 风格年份


def _float_or_none(raw: str | None) -> float | None:
    if raw is None or not str(raw).strip():
        return None
    return float(str(raw).strip())


def _read(key: str, settings: Settings, data_dir: Path | None) -> str:
    """读一个数据源：data_dir 给定时走本地文件（离线），否则 HTTP 直连。"""
    if data_dir is not None:
        path = data_dir / Path(PATHS[key]).name
        if not path.is_file():
            raise FileNotFoundError(f"本地缓存缺少 {path}（--data-dir 布局应含 {Path(PATHS[key]).name}）")
        return path.read_text(encoding="utf-8")

    import httpx  # 延迟导入：离线路径不依赖网络栈

    url = f"{settings.atlas_base.rstrip('/')}/{PATHS[key]}"
    kwargs: dict = {
        "headers": {"User-Agent": UA},
        "timeout": 60.0,
        "follow_redirects": True,
    }
    if settings.http_proxy:
        kwargs["proxy"] = settings.http_proxy
    with httpx.Client(**kwargs) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return resp.text


def parse_philosophers(text: str) -> list[dict]:
    out: list[dict] = []
    for row in csv.DictReader(io.StringIO(text)):
        name_pt = row["name"].strip()
        name_en, name_zh = FAMOUS_NAMES.get(name_pt, (None, None))
        school = row["school"].strip()
        region = row["region"].strip()
        rec = {
            "atlas_id": int(row["id"]),
            "name_pt": name_pt,
            "name_en": name_en,
            "name_zh": name_zh,
            "birth_year": _int_or_none(row.get("birth_year")),
            "death_year": _int_or_none(row.get("death_year")),
            "school": school,
            "school_en": SCHOOL_EN.get(school),
            "school_zh": SCHOOL_ZH.get(school),
            "region": region,
            "region_en": REGION_EN.get(region),
            "region_zh": REGION_ZH.get(region),
            **{axis: _float_or_none(row.get(axis)) for axis in AXES},
            "influence_score": _float_or_none(row.get("influence_score")),
        }
        out.append(rec)
    if not out:
        raise ValueError("philosophers.csv 解析结果为空（表头或数据不符）")
    return out


def parse_influences(text: str, valid_ids: set[int]) -> tuple[list[dict], list[str]]:
    edges: list[dict] = []
    skipped: list[str] = []
    for row in csv.DictReader(io.StringIO(text)):
        polarity = (row.get("type") or "").strip()
        if polarity not in POLARITIES:
            # 数据质量门：白皮书口径只有三种极性，出现未知值宁可失败也不静默入库
            raise ValueError(
                f"influences.csv 出现未知极性 {polarity!r}（口径仅 positive/critical/synthetic），拒绝入库"
            )
        try:
            source_id = int(row["source"])
            target_id = int(row["target"])
            weight = float(row["weight"])
        except (KeyError, TypeError, ValueError) as exc:
            skipped.append(f"unparseable row {dict(row)}: {exc}")
            continue
        if source_id not in valid_ids or target_id not in valid_ids:
            skipped.append(f"dangling endpoint {source_id}->{target_id}")
            continue
        edges.append(
            {"source_id": source_id, "target_id": target_id, "weight": weight, "polarity": polarity}
        )
    return edges, skipped


def parse_topics(text: str) -> list[dict]:
    out: list[dict] = []
    for item in json.loads(text):
        out.append(
            {
                "topic_id": item["id"],
                "slug": item.get("slug"),
                # 源目录只有 pt 标签；en/zh 留 NULL，后续任务补
                "label_pt": item.get("label"),
                "label_en": None,
                "label_zh": None,
                "category": item.get("category"),
                "description": item.get("description"),
                "key_philosophers": json.dumps(item.get("key_philosophers") or [], ensure_ascii=False),
            }
        )
    return out


def _log_failed(settings: Settings, message: str) -> None:
    """摄取失败也留台账（best-effort，DB 打不开时只打 stderr）。"""
    try:
        conn = settings.connect()
        try:
            schema.init(conn)
            with conn:
                conn.execute(
                    "INSERT INTO ingest_log (source, status, detail, as_of) VALUES (?,?,?,?)",
                    (
                        SOURCE,
                        "failed",
                        json.dumps({"error": message}, ensure_ascii=False),
                        datetime.now(timezone.utc).isoformat(timespec="microseconds"),
                    ),
                )
        finally:
            conn.close()
    except Exception as exc:  # noqa: BLE001 台账失败不掩盖原错误
        print(f"[ingest_atlas] 失败台账写入失败：{exc}", file=sys.stderr)


def run(settings: Settings | None = None, data_dir: str | Path | None = None) -> dict:
    """执行一次摄取，返回计数明细（含 status: ok / partial）。"""
    settings = settings or Settings.from_env()
    dd = Path(data_dir) if data_dir is not None else None

    try:
        philosophers = parse_philosophers(_read("philosophers", settings, dd))
        valid_ids = {p["atlas_id"] for p in philosophers}
        edges, skipped = parse_influences(_read("influences", settings, dd), valid_ids)
        topics = parse_topics(_read("topics", settings, dd))
    except Exception as exc:  # noqa: BLE001
        _log_failed(settings, f"{type(exc).__name__}: {exc}")
        raise

    unmapped_schools = sorted(
        {p["school"] for p in philosophers if p["school_en"] is None or p["school_zh"] is None}
    )
    unmapped_regions = sorted(
        {p["region"] for p in philosophers if p["region_en"] is None or p["region_zh"] is None}
    )
    status = "ok" if not (skipped or unmapped_schools or unmapped_regions) else "partial"
    now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    detail = {
        "philosophers": len(philosophers),
        "relations": len(edges),
        "topics": len(topics),
        "skipped_relations": skipped,
        "unmapped_schools": unmapped_schools,
        "unmapped_regions": unmapped_regions,
        "as_of": now,
    }

    conn = settings.connect()
    try:
        schema.init(conn)
        with conn:  # 单事务：DELETE 后重插 → 幂等；wikidata_qid 重置为 NULL（2.3 负责回填）
            conn.execute("DELETE FROM philosophers")
            conn.execute("DELETE FROM relations")
            conn.execute("DELETE FROM topics")
            placeholders = ", ".join(["?"] * len(PHILOSOPHER_COLUMNS))
            conn.executemany(
                f"INSERT INTO philosophers ({', '.join(PHILOSOPHER_COLUMNS)}, wikidata_qid, ingested_at) "
                f"VALUES ({placeholders}, NULL, ?)",
                [tuple(p[c] for c in PHILOSOPHER_COLUMNS) + (now,) for p in philosophers],
            )
            conn.executemany(
                "INSERT INTO relations (source_id, target_id, weight, polarity, ingested_at) "
                "VALUES (?,?,?,?,?)",
                [
                    (e["source_id"], e["target_id"], e["weight"], e["polarity"], now)
                    for e in edges
                ],
            )
            conn.executemany(
                "INSERT INTO topics (topic_id, slug, label_pt, label_en, label_zh, category, "
                "description, key_philosophers, ingested_at) VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (
                        t["topic_id"], t["slug"], t["label_pt"], t["label_en"], t["label_zh"],
                        t["category"], t["description"], t["key_philosophers"], now,
                    )
                    for t in topics
                ],
            )
            conn.execute(
                "INSERT INTO ingest_log (source, status, detail, as_of) VALUES (?,?,?,?)",
                (SOURCE, status, json.dumps(detail, ensure_ascii=False), now),
            )
    finally:
        conn.close()

    detail["status"] = status
    return detail


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Philosophy Atlas 种子图谱摄取（task 2.2）")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="本地缓存目录（应含 philosophers.csv / influences.csv / topics.json），离线用",
    )
    parser.add_argument(
        "--db",
        default=None,
        help="SQLite 路径（默认 FD_ZHESI_REF_DB 或 ~/.fd-zhesi/reference.db）",
    )
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.db:
        settings.db_path = args.db

    detail = run(settings, args.data_dir)
    print(json.dumps(detail, ensure_ascii=False, indent=2))
    return 0 if detail["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
