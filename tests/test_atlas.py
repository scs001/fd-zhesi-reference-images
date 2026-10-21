"""task 2.2 离线单测：Atlas 摄取器 + 工具接线契约。全程不联网
（fixture 为内嵌 CSV/JSON 样本，经 --data-dir 路径摄取）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO / "scripts"
if str(SCRIPTS_DIR) not in sys.path:  # ingest_atlas / atlas_labels 是脚本不是包
    sys.path.insert(0, str(SCRIPTS_DIR))
if str(REPO) not in sys.path:  # 允许从任意 cwd 跑 pytest
    sys.path.insert(0, str(REPO))

import ingest_atlas  # noqa: E402
from fd_zhesi_reference import schema  # noqa: E402
from fd_zhesi_reference.config import Settings  # noqa: E402
from fd_zhesi_reference.errors import HonestError  # noqa: E402
from fd_zhesi_reference.server import create_server  # noqa: E402

# ------------------------------------------------------------- fixtures ----
# 内嵌样本（结构与线上文件一致；id 取自真实数据：2 Heráclito / 3 Parmênides /
# 4 Anaximandro / 6 Sócrates / 7 Platão / 8 Aristóteles）
PHILOSOPHERS_CSV = (
    "id,name,birth_year,death_year,school,region,individualism,materialism,rationalism,"
    "determinism,transcendence,universalism,influence_score\n"
    "6,Sócrates,-470,-399.0,Clássico Grego,Grécia,6,1,9,4,7,9,10\n"
    "7,Platão,-428,-348.0,Clássico Grego,Grécia,3,2,10,5,9,10,10\n"
    "8,Aristóteles,-384,-322.0,Clássico Grego,Grécia,5,7,10,6,4,8,10\n"
    "3,Parmênides,-510,-450.0,Pré-socrático,Grécia,4,2,10,10,7,10,7\n"
    "2,Heráclito,-535,-475.0,Pré-socrático,Grécia,4,6,8,9,5,9,7\n"
    "4,Anaximandro,-610,-546.0,Pré-socrático,Grécia,3,5,6,5,4,6,5\n"
)
INFLUENCES_CSV = (
    "source,target,weight,type\n"
    "3,2,0.6,critical\n"   # Parmênides → Heráclito（正向，批判）
    "7,3,0.5,positive\n"   # Platão → Parmênides（查询 Parmênides/Platão 时是反向边）
    "6,7,0.9,positive\n"
)
TOPICS_JSON = (
    "[{\"id\": \"T99\", \"slug\": \"teste\", \"label\": \"Teste\", \"category\": \"Ética\", "
    "\"description\": \"descrição de teste\", \"contemporary_context\": \"contexto\", "
    "\"key_philosophers\": [6, 7]}]"
)


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "philosophers.csv").write_text(PHILOSOPHERS_CSV, encoding="utf-8")
    (tmp_path / "influences.csv").write_text(INFLUENCES_CSV, encoding="utf-8")
    (tmp_path / "topics.json").write_text(TOPICS_JSON, encoding="utf-8")
    return tmp_path


@pytest.fixture()
def ingested(tmp_path: Path, data_dir: Path) -> Settings:
    settings = Settings(db_path=str(tmp_path / "ref.db"))
    ingest_atlas.run(settings, data_dir=data_dir)
    return settings


async def call_tool(mcp, name: str, /, **args) -> dict:
    tools = await mcp.list_tools()
    fn = next(t.fn for t in tools if t.name == name)
    return await fn(**args)


# --------------------------------------------------------------- ingest ----
def test_ingest_counts_log_and_idempotency(ingested: Settings, data_dir: Path) -> None:
    conn = ingested.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM philosophers").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == 1
        log = conn.execute(
            "SELECT source, status, detail FROM ingest_log ORDER BY as_of DESC"
        ).fetchone()
        assert log["source"] == "philosophy-atlas" and log["status"] == "ok"
        # wikidata_qid 恒为 NULL：该列归 task 2.3 的 Wikidata 对齐任务管
        assert conn.execute(
            "SELECT COUNT(*) FROM philosophers WHERE wikidata_qid IS NOT NULL"
        ).fetchone()[0] == 0
    finally:
        conn.close()

    ingest_atlas.run(ingested, data_dir=data_dir)  # 重跑幂等
    conn = ingested.connect()
    try:
        assert conn.execute("SELECT COUNT(*) FROM philosophers").fetchone()[0] == 6
        assert conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0] == 3
        assert conn.execute("SELECT COUNT(*) FROM topics").fetchone()[0] == 1
    finally:
        conn.close()


def test_ingest_applies_label_maps(ingested: Settings) -> None:
    conn = ingested.connect()
    try:
        row = conn.execute(
            "SELECT name_en, name_zh, school_en, school_zh, region_en, region_zh "
            "FROM philosophers WHERE atlas_id=6"
        ).fetchone()
        assert tuple(row) == (
            "Socrates", "苏格拉底", "Classical Greek", "古典希腊", "Greece", "希腊",
        )
        unknown = conn.execute(
            "SELECT name_en, name_zh FROM philosophers WHERE atlas_id=4"
        ).fetchone()  # Anaximandro 不在 60 人小字典 → NULL（宁缺毋滥）
        assert tuple(unknown) == (None, None)
    finally:
        conn.close()


def test_ingest_rejects_unknown_polarity(tmp_path: Path, data_dir: Path) -> None:
    (data_dir / "influences.csv").write_text(
        "source,target,weight,type\n6,7,0.5,hostile\n", encoding="utf-8"
    )
    settings = Settings(db_path=str(tmp_path / "bad.db"))
    with pytest.raises(ValueError, match="hostile"):
        ingest_atlas.run(settings, data_dir=data_dir)


# ----------------------------------------------------- philosopher_query ----
async def test_philosopher_query_hit_with_provenance(ingested: Settings) -> None:
    mcp = create_server(ingested)
    res = await call_tool(mcp, "philosopher_query", name="Sócrates")
    assert len(res["results"]) == 1
    hit = res["results"][0]
    assert hit["atlas_id"] == 6
    assert hit["name"] == {"pt": "Sócrates", "en": "Socrates", "zh": "苏格拉底"}
    assert hit["birth_year"] == -470 and hit["death_year"] == -399
    assert hit["school"]["zh"] == "古典希腊"
    assert hit["region"]["pt"] == "Grécia"
    assert hit["axes"] == {
        "individualism": 6.0, "materialism": 1.0, "rationalism": 9.0,
        "determinism": 4.0, "transcendence": 7.0, "universalism": 9.0,
    }
    assert hit["influence_score"] == 10.0
    assert hit["wikidata_qid"] is None
    prov = hit["provenance"]
    assert prov["source"] == "philosophy-atlas"
    assert prov["stable_id"] == "atlas:6"
    assert "CC BY 4.0" in prov["attribution"]  # 署名随行
    assert prov["as_of"]

    # 大小写/带音符不敏感的包含匹配，英文名也能命中
    for q in ("sócrates", "SOCRATES", "Socrates"):
        assert (await call_tool(mcp, "philosopher_query", name=q))["results"]
    # 子串：中文“柏拉图”也应命中 Platão
    assert (await call_tool(mcp, "philosopher_query", name="柏拉图"))["results"]


async def test_philosopher_query_not_indexed(ingested: Settings) -> None:
    mcp = create_server(ingested)
    res = await call_tool(mcp, "philosopher_query", name="Fulano Inexistente")
    assert res["results"] == []
    assert res["reason"] == "not_indexed"
    assert res["hint"]


async def test_philosopher_query_not_ingested(tmp_path: Path) -> None:
    settings = Settings(db_path=str(tmp_path / "empty.db"))
    conn = settings.connect()
    schema.init(conn)  # 建表但不摄取
    conn.close()
    mcp = create_server(settings)
    res = await call_tool(mcp, "philosopher_query", name="Sócrates")
    assert res == {
        "results": [],
        "reason": "not_ingested",
        "hint": "Atlas 种子图谱尚未摄取（运行 scripts/ingest_atlas.py 后可查）",
    }


# -------------------------------------------------------- relation_query ----
async def test_relation_query_hit_forward_critical(ingested: Settings) -> None:
    mcp = create_server(ingested)
    res = await call_tool(mcp, "relation_query", source="Parmênides", target="Heráclito")
    assert len(res["results"]) == 1
    edge = res["results"][0]
    assert edge["from"] == 3 and edge["to"] == 2
    assert edge["from_name"] == "Parmênides" and edge["to_name"] == "Heráclito"
    assert edge["direction"] == "forward"
    assert edge["polarity"] == "critical"
    assert edge["weight"] == 0.6
    assert "CC BY 4.0" in edge["provenance"]["attribution"]
    assert res["source"][0]["name"]["pt"] == "Parmênides"
    assert res["target"][0]["name"]["pt"] == "Heráclito"
    assert res["source"][0]["provenance"]["stable_id"] == "atlas:3"


async def test_relation_query_reverse_edge_annotated(ingested: Settings) -> None:
    mcp = create_server(ingested)
    # 图中只有 Platão→Parmênides；按 Parmênides,Platão 查询应标注 reverse
    res = await call_tool(mcp, "relation_query", source="Parmênides", target="Platão")
    assert len(res["results"]) == 1
    edge = res["results"][0]
    assert edge["direction"] == "reverse"
    assert edge["from"] == 7 and edge["to"] == 3
    assert edge["polarity"] == "positive"


async def test_relation_query_unknown_entity(ingested: Settings) -> None:
    mcp = create_server(ingested)
    res = await call_tool(mcp, "relation_query", source="Fulano", target="Sócrates")
    assert res["results"] == []
    assert res["reason"] == "unknown_entity"
    assert "Fulano" in res["hint"]
    res2 = await call_tool(mcp, "relation_query", source="Sócrates", target="Beltrano")
    assert res2["reason"] == "unknown_entity"
    assert "Beltrano" in res2["hint"]


async def test_relation_query_known_people_no_edge(ingested: Settings) -> None:
    mcp = create_server(ingested)
    res = await call_tool(mcp, "relation_query", source="Aristóteles", target="Heráclito")
    assert res["results"] == []
    assert res["reason"] == "not_indexed"
    assert res["hint"]
    assert res["source"][0]["name"]["pt"] == "Aristóteles"  # 人物摘要仍随行
    assert res["target"][0]["name"]["pt"] == "Heráclito"


# ------------------------------------------------------- 骨架契约兼容性 ----
async def test_missing_table_still_honest_error() -> None:
    """:memory: 库从未初始化（骨架状态）→ 保持骨架期诚实错误，不编造。"""
    mcp = create_server(Settings(db_path=":memory:"))
    with pytest.raises(HonestError) as exc:
        await call_tool(mcp, "philosopher_query", name="Hume")
    assert exc.value.tool == "philosopher_query" and exc.value.tracking_task
    with pytest.raises(HonestError) as exc2:
        await call_tool(mcp, "relation_query", source="Hume", target="Kant")
    assert exc2.value.tool == "relation_query" and exc2.value.tracking_task
