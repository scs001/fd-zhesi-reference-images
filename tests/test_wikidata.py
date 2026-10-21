"""task 2.3 离线测试：归一/年份解析/实体解析/职业过滤/打分/选优/工件汇编。

全部离线：SPARQL 与 Action API 的响应样本内嵌为 fixture，不联网。
样本形状与 2026-10 实测响应一致（wbsearchentities / wbgetentities /
SPARQL 批量 EntitySearch + label service / 子类闭包小查询）。
"""

from __future__ import annotations

from fd_zhesi_reference.wikidata import (
    CONFIDENCE_HIGH,
    CONFIDENCE_MEDIUM,
    SCORE_THRESHOLD,
    AtlasPhilosopher,
    Candidate,
    apply_occupation_filter,
    build_alignment,
    build_sparql_batch_query,
    candidate_from_entity,
    choose_best,
    load_atlas_philosophers,
    normalize_name,
    parse_sparql_batch,
    parse_sparql_closure,
    parse_wbsearch,
    score_candidate,
    sparql_time_to_year,
    strip_accents,
    unaligned_reason,
)

# ---------------------------------------------------------------- fixtures

ATLAS_CSV = """id,name,birth_year,death_year,school,region,individualism,materialism,rationalism,determinism,transcendence,universalism,influence_score
1,Tales de Mileto,-625,-545.0,Pré-socrático,Grécia,5,8,7,6,3,7,6
2,Erasmo de Roterdã,1466,1536,Renascimento,Holanda,7,4,8,5,3,6,8
3,Kant,1724,1804,Iluminismo,Alemanha,6,4,10,9,2,6,10
4,João Ninguém Da Silva,1900,,Escolástica,Portugal,5,5,5,5,5,5,1
"""

# wbgetentities 单实体样本（Erasmus Q43499 的关键剪裁：多 P569 值、别名命中、zh 标签）
ENTITY_ERASMUS = {
    "type": "item",
    "id": "Q43499",
    "labels": {
        "pt": {"language": "pt", "value": "Erasmo de Roterdão"},
        "en": {"language": "en", "value": "Erasmus"},
        "zh": {"language": "zh", "value": "德西德里乌斯·伊拉斯谟"},
    },
    "descriptions": {
        "en": {"language": "en", "value": "Dutch Renaissance humanist (1466-1536)"},
        "pt": {"language": "pt", "value": "Teólogo e filósofo humanista"},
    },
    "aliases": {
        "pt": [
            {"language": "pt", "value": "Erasmo de Roterdam"},
            {"language": "pt", "value": "Erasmo de Roterdã"},
        ],
        "en": [{"language": "en", "value": "Erasmus of Rotterdam"}],
    },
    "claims": {
        "P569": [
            {"mainsnak": {"snaktype": "value", "property": "P569",
                          "datavalue": {"value": {"time": "+1469-10-28T00:00:00Z", "precision": 9}}},
             "rank": "normal", "type": "statement"},
            {"mainsnak": {"snaktype": "value", "property": "P569",
                          "datavalue": {"value": {"time": "+1466-10-28T00:00:00Z", "precision": 9}}},
             "rank": "preferred", "type": "statement"},
            {"mainsnak": {"snaktype": "value", "property": "P569",
                          "datavalue": {"value": {"time": "+1400-01-01T00:00:00Z", "precision": 9}}},
             "rank": "deprecated", "type": "statement"},
        ],
        "P570": [
            {"mainsnak": {"snaktype": "value", "property": "P570",
                          "datavalue": {"value": {"time": "+1536-07-12T00:00:00Z", "precision": 11}}},
             "rank": "normal", "type": "statement"},
        ],
        "P106": [
            {"mainsnak": {"snaktype": "value", "property": "P106",
                          "datavalue": {"value": {"entity-type": "item", "id": "Q49757"}}},
             "rank": "normal", "type": "statement"},
            {"mainsnak": {"snaktype": "value", "property": "P106",
                          "datavalue": {"value": {"entity-type": "item", "id": "Q36180"}}},
             "rank": "normal", "type": "statement"},
        ],
    },
}

# BC 纪年实体（Thales）：负年份 + 无 zh 标签 + snaktype novalue 的 P570
ENTITY_THALES = {
    "type": "item",
    "id": "Q41763",
    "labels": {"pt": {"language": "pt", "value": "Tales de Mileto"},
               "en": {"language": "en", "value": "Thales of Miletus"}},
    "aliases": {"en": [{"language": "en", "value": "Thales"}]},
    "claims": {
        "P569": [{"mainsnak": {"snaktype": "value", "property": "P569",
                               "datavalue": {"value": {"time": "-0626-00-00T00:00:00Z"}}},
                  "rank": "normal", "type": "statement"}],
        "P570": [
            {"mainsnak": {"snaktype": "novalue", "property": "P570"}, "rank": "normal", "type": "statement"},
            {"mainsnak": {"snaktype": "value", "property": "P570",
                          "datavalue": {"value": {"time": "-0547-00-00T00:00:00Z"}}},
             "rank": "normal", "type": "statement"},
        ],
        "P106": [{"mainsnak": {"snaktype": "value", "property": "P106",
                               "datavalue": {"value": {"entity-type": "item", "id": "Q49757"}}},
                  "rank": "normal", "type": "statement"}],
    },
}

# Immanuel Kant：pt 标签就是 "Kant"（单名）；无别名
ENTITY_KANT = {
    "type": "item",
    "id": "Q937",
    "labels": {"pt": {"language": "pt", "value": "Kant"},
               "en": {"language": "en", "value": "Immanuel Kant"},
               "zh-hans": {"language": "zh-hans", "value": "伊曼努尔·康德"}},
    "claims": {
        "P569": [{"mainsnak": {"snaktype": "value", "property": "P569",
                               "datavalue": {"value": {"time": "+1724-04-22T00:00:00Z"}}},
                  "rank": "normal", "type": "statement"}],
        "P570": [{"mainsnak": {"snaktype": "value", "property": "P570",
                               "datavalue": {"value": {"time": "+1804-02-12T00:00:00Z"}}},
                  "rank": "normal", "type": "statement"}],
        "P106": [{"mainsnak": {"snaktype": "value", "property": "P106",
                               "datavalue": {"value": {"entity-type": "item", "id": "Q49757"}}},
                  "rank": "normal", "type": "statement"}],
    },
}

WBSEARCH_ERASMUS = {
    "search": [
        {"id": "Q43499", "label": "Erasmus", "description": "Dutch Renaissance humanist",
         "match": {"type": "alias", "language": "pt", "text": "Erasmo de Roterdã"}},
        {"id": "Q152523", "label": "Erasmus (disambiguation)", "match": {"type": "label"}},
        {"id": "Q99999", "label": "Erasmus Programme", "match": {"type": "label"}},
    ],
    "searchinfo": {"search": "Erasmo de Roterdã"},
    "success": 1,
}

SPARQL_BATCH_SAMPLE = {
    "head": {"vars": ["name", "item", "labelPt", "labelEn", "labelZh", "altPt", "altEn", "occ", "birth", "death"]},
    "results": {"bindings": [
        {  # 同名同实体多行：多职业/多生卒值（OPTIONAL 笛卡尔积的真实形态）
            "name": {"type": "literal", "value": "Tales de Mileto"},
            "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q41763"},
            "labelPt": {"type": "literal", "xml:lang": "pt", "value": "Tales de Mileto"},
            "labelEn": {"type": "literal", "xml:lang": "en", "value": "Thales of Miletus"},
            "altEn": {"type": "literal", "value": "Thales, Thales the Athenian"},
            "occ": {"type": "uri", "value": "http://www.wikidata.org/entity/Q49757"},
            "birth": {"datatype": "http://www.w3.org/2001/XMLSchema#dateTime",
                      "type": "literal", "value": "-0626-00-00T00:00:00Z"},
            "death": {"datatype": "http://www.w3.org/2001/XMLSchema#dateTime",
                      "type": "literal", "value": "-0547-00-00T00:00:00Z"},
        },
        {
            "name": {"type": "literal", "value": "Tales de Mileto"},
            "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q41763"},
            "labelPt": {"type": "literal", "xml:lang": "pt", "value": "Tales de Mileto"},
            "labelEn": {"type": "literal", "xml:lang": "en", "value": "Thales of Miletus"},
            "occ": {"type": "uri", "value": "http://www.wikidata.org/entity/Q4964182"},
            "birth": {"datatype": "http://www.w3.org/2001/XMLSchema#dateTime",
                      "type": "literal", "value": "-0624-00-00T00:00:00Z"},
        },
        {  # 干扰项：同搜索词下的另一位哲学家
            "name": {"type": "literal", "value": "Tales de Mileto"},
            "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q999001"},
            "labelPt": {"type": "literal", "xml:lang": "pt", "value": "Tales, o ateniense"},
            "labelEn": {"type": "literal", "xml:lang": "en", "value": "Thales of Athens"},
            "occ": {"type": "uri", "value": "http://www.wikidata.org/entity/Q49757"},
            "birth": {"datatype": "http://www.w3.org/2001/XMLSchema#dateTime",
                      "type": "literal", "value": "+0500-00-00T00:00:00Z"},
        },
    ]},
}

SPARQL_CLOSURE_SAMPLE = {
    "head": {"vars": ["occ"]},
    "results": {"bindings": [
        {"occ": {"type": "uri", "value": "http://www.wikidata.org/entity/Q49757"}},
        {"occ": {"type": "uri", "value": "http://www.wikidata.org/entity/Q41775"}},
        {"occ": {"type": "uri", "value": "http://www.wikidata.org/entity/Q123456"}},
    ]},
}


# ---------------------------------------------------------------- 归一与年份


def test_strip_accents_and_normalize() -> None:
    assert strip_accents("Erasmo de Roterdã") == "Erasmo de Roterdã".replace("ã", "a")
    assert normalize_name("Erasmo de Roterdã") == "erasmo de roterda"
    assert normalize_name("Pseudo-Dionísio Areopagita") == "pseudo dionisio areopagita"
    assert normalize_name("Jean-Jacques  Rousseau") == "jean jacques rousseau"
    assert normalize_name("Duns Scotus") == "duns scotus"
    assert normalize_name("") == ""


def test_sparql_time_to_year() -> None:
    assert sparql_time_to_year("-0624-00-00T00:00:00Z") == -624
    assert sparql_time_to_year("+1872-05-18T00:00:00Z") == 1872
    assert sparql_time_to_year("1466-10-28T00:00:00Z") == 1466
    assert sparql_time_to_year("not-a-date") is None
    assert sparql_time_to_year(None) is None


def test_load_atlas_philosophers() -> None:
    rows = load_atlas_philosophers(ATLAS_CSV)
    assert len(rows) == 4
    first = rows[0]
    assert (first.atlas_id, first.name, first.birth_year, first.death_year) == (1, "Tales de Mileto", -625, -545)
    assert rows[3].death_year is None  # 空 death_year → None
    assert rows[1].death_year == 1536  # "1536"（字符串）与 "-545.0"（浮点）都收


# ------------------------------------------------------------- 实体与搜索解析


def test_candidate_from_entity_full() -> None:
    cand = candidate_from_entity("Q43499", ENTITY_ERASMUS)
    assert cand.qid == "Q43499"
    assert cand.label_pt == "Erasmo de Roterdão"
    assert cand.label_en == "Erasmus"
    assert cand.label_zh == "德西德里乌斯·伊拉斯谟"
    assert "Erasmo de Roterdã" in cand.aliases_pt
    assert cand.births == (1469, 1466)  # 弃用值 1400 被排除
    assert cand.deaths == (1536,)
    assert "Q49757" in cand.occupations
    assert cand.display_name_en() == "Erasmus"


def test_candidate_from_entity_bc_and_missing() -> None:
    cand = candidate_from_entity("Q41763", ENTITY_THALES)
    assert cand.births == (-626,)
    assert cand.deaths == (-547,)  # novalue 行被跳过
    assert cand.label_zh is None  # 无 zh 标签 → None（不硬翻）
    assert cand.display_name_en() == "Thales of Miletus"

    empty = candidate_from_entity("Q1", {"type": "item", "id": "Q1"})
    assert empty.qid == "Q1" and empty.births == () and empty.label_pt is None


def test_candidate_from_entity_zh_variants() -> None:
    cand = candidate_from_entity("Q937", ENTITY_KANT)
    assert cand.label_zh == "伊曼努尔·康德"  # zh-hans 兜底


def test_parse_wbsearch() -> None:
    qids = parse_wbsearch(WBSEARCH_ERASMUS)
    assert qids == ["Q43499", "Q152523", "Q99999"]
    assert parse_wbsearch({"search": []}) == []
    assert parse_wbsearch({}) == []


# ---------------------------------------------------------------- 职业过滤


def test_occupation_label_pattern() -> None:
    from fd_zhesi_reference.wikidata import occupation_label_is_philosopher

    assert occupation_label_is_philosopher(["philosopher", "filósofo"])
    assert occupation_label_is_philosopher(["pre-Socratic philosopher"])
    assert occupation_label_is_philosopher(["natural philosopher"])
    assert occupation_label_is_philosopher(["filósofo da antiguidade"])
    assert not occupation_label_is_philosopher(["mathematician", "matemático"])
    assert not occupation_label_is_philosopher(["politician"])
    assert not occupation_label_is_philosopher([])


def test_occupation_filter_closure() -> None:
    closure = {"Q49757", "Q41775"}
    phil = Candidate(qid="Q1", occupations=("Q49757",))
    sub = Candidate(qid="Q2", occupations=("Q36180", "Q41775"))
    other = Candidate(qid="Q3", occupations=("Q36180",))
    kept, mode = apply_occupation_filter([phil, sub, other], closure)
    assert mode == "closure"
    assert {c.qid for c in kept} == {"Q1", "Q2"}


def test_occupation_filter_rescues_ontology_hole() -> None:
    """本体洞兜底：Q4964182 label 就是 philosopher 却不在 Q49757 闭包里——
    靠职业标签措辞信号救回（泰勒斯/毕达哥拉斯们的真实处境）。"""
    closure = {"Q49757"}
    thales = Candidate(qid="Q36303", occupations=("Q4964182", "Q170790"))  # Q4964182=philosopher 重复项
    kept, mode = apply_occupation_filter([thales], closure, philosopher_occupations={"Q4964182"})
    assert mode == "closure" and kept and kept[0].occupation_ok

    # 不给标签集时，描述措辞是第三道信号
    desc_only = Candidate(qid="Q5", occupations=("Q170790",),
                          descriptions=("Ancient Greek philosopher and polymath",))
    kept2, _ = apply_occupation_filter([desc_only], closure)
    assert [c.qid for c in kept2] == ["Q5"]

    # 三信号全不中的严格剔除
    boxer = Candidate(qid="Q6", occupations=("Q11338576",), descriptions=("Brazilian boxer",))
    kept3, _ = apply_occupation_filter([boxer], closure, {"Q4964182"})
    assert kept3 == []


def test_occupation_filter_loose() -> None:
    phil = Candidate(qid="Q1", occupations=("Q49757",))
    desc_hit = Candidate(qid="Q2", occupations=(), descriptions=("Greek philosopher",))
    desc_pt = Candidate(qid="Q3", occupations=(), descriptions=("Teólogo e filósofo",))
    mystery = Candidate(qid="Q4", occupations=(), descriptions=("French mathematician",))
    kept, mode = apply_occupation_filter([phil, desc_hit, desc_pt, mystery], None)
    assert mode == "loose"
    assert {c.qid for c in kept} == {"Q1", "Q2", "Q3", "Q4"}
    by_qid = {c.qid: c for c in kept}
    assert by_qid["Q1"].occupation_ok and by_qid["Q2"].occupation_ok
    assert by_qid["Q4"].occupation_ok is False  # 无从验证：保留但打分时降标记


# ------------------------------------------------------------------- 打分


def _atlas(**kwargs) -> AtlasPhilosopher:
    return AtlasPhilosopher(**kwargs)


def test_score_pt_label_exact_plus_years() -> None:
    atlas = _atlas(atlas_id=1, name="Tales de Mileto", birth_year=-625, death_year=-545)
    cand = candidate_from_entity("Q41763", ENTITY_THALES)
    scored = score_candidate(atlas, cand)
    # pt 精确 40 + 生年 ±2（-625 vs -626）20 + 卒年 ±2（-545 vs -547）20
    assert scored.score == 80
    assert scored.reasons == ["pt_label_exact", "birth_year", "death_year"]
    assert scored.method == "pt_label_exact+birth_year+death_year"
    assert scored.confidence == "high"


def test_score_alias_exact_beats_label_near() -> None:
    atlas = _atlas(atlas_id=2, name="Erasmo de Roterdã", birth_year=1466, death_year=1536)
    cand = candidate_from_entity("Q43499", ENTITY_ERASMUS)
    scored = score_candidate(atlas, cand)
    # 名字证据取最大：alias_exact(35) > contains(15)；生卒年双命中 +40
    assert scored.score == 35 + 20 + 20
    assert "alias_exact" in scored.reasons
    assert scored.confidence == "high"


def test_score_contains_single_token() -> None:
    atlas = _atlas(atlas_id=3, name="Kant", birth_year=1724, death_year=1804)
    # pt 标签恰好就是 "Kant" → 精确；换成 en 视角构造 contains 场景：
    cand = Candidate(qid="Q937", label_en="Immanuel Kant", births=(1724,), deaths=(1804,))
    scored = score_candidate(atlas, cand)
    assert "contains" in scored.reasons
    assert scored.score == 15 + 40  # contains + 双年份
    assert scored.confidence == "medium"


def test_score_year_mismatch_penalty() -> None:
    atlas = _atlas(atlas_id=9, name="Thales of Miletus", birth_year=-625, death_year=-545)
    wrong_century = Candidate(qid="Q1", label_en="Thales of Miletus", births=(500,), deaths=(560,))
    scored = score_candidate(atlas, wrong_century)
    assert scored.score == 35 - 15 - 15  # en 名字精确但生卒都错代（低于阈值，不会中选）


def test_score_year_scholarly_disagreement_neutral() -> None:
    """上古生卒的学派分歧（5~30 年）不奖不罚：帕门尼德的真实处境。

    Atlas -510/-450 vs Wikidata Q125551 -515/-470：名字精确 40，年份灰区，
    总分 40 过阈值（low），不会被错配罚掉。
    """
    atlas = _atlas(atlas_id=3, name="Parmênides", birth_year=-510, death_year=-450)
    parmenides = Candidate(qid="Q125551", label_pt="Parménides", births=(-515,), deaths=(-470,))
    scored = score_candidate(atlas, parmenides)
    assert scored.score == 40
    assert scored.confidence == "low"
    assert scored.method == "pt_label_exact"


def test_score_years_only_passes_low() -> None:
    atlas = _atlas(atlas_id=9, name="Zhang San", birth_year=100, death_year=160)
    cand = Candidate(qid="Q1", label_en="Completely Different Name", births=(101,), deaths=(158,))
    scored = score_candidate(atlas, cand)
    assert scored.score == 40
    assert scored.confidence == "low"  # 过阈值但只到 low，工件可人工复核


def test_choose_best_and_threshold() -> None:
    atlas = _atlas(atlas_id=1, name="Tales de Mileto", birth_year=-625, death_year=-545)
    best = choose_best(atlas, [candidate_from_entity("Q41763", ENTITY_THALES)])
    assert best is not None and best.candidate.qid == "Q41763"

    weak = [Candidate(qid="Q2", label_en="Unrelated", births=(1900,))]
    assert choose_best(atlas, weak) is None
    assert unaligned_reason(atlas, weak).startswith("below_threshold")
    assert unaligned_reason(atlas, []) == "no_candidates"


def test_choose_best_tiebreak_deterministic() -> None:
    atlas = _atlas(atlas_id=1, name="Platão", birth_year=-427, death_year=-347)
    a = Candidate(qid="Q100", label_pt="Platão", births=(-427,), deaths=(-347,))
    b = Candidate(qid="Q99", label_pt="Platão", births=(-427,), deaths=(-347,))
    assert score_candidate(atlas, a).score == score_candidate(atlas, b).score
    best = choose_best(atlas, [a, b])
    assert best.candidate.qid == "Q100"  # 同分按 QID 字典序，确定性（"Q100" < "Q99"）


def test_confidence_boundaries() -> None:
    assert CONFIDENCE_HIGH == 70 and CONFIDENCE_MEDIUM == 50 and SCORE_THRESHOLD == 35
    # 无生卒佐证的纯名字证据：pt 精确 40 → 过阈值但只到 low（可人工复核）
    atlas = _atlas(atlas_id=1, name="Kant")
    cand = Candidate(qid="Q937", label_pt="Kant")
    assert score_candidate(atlas, cand).confidence == "low"
    # 名字 + 双年份佐证才进 medium/high
    atlas_dated = _atlas(atlas_id=1, name="Kant", birth_year=1724, death_year=1804)
    full = Candidate(qid="Q937", label_pt="Kant", births=(1724,), deaths=(1804,))
    assert score_candidate(atlas_dated, full).confidence == "high"


# --------------------------------------------------------------- SPARQL 通道


def test_build_sparql_batch_query_escapes() -> None:
    query = build_sparql_batch_query(['Platão', 'He said "hi" \\ back'], lang="pt", limit=8)
    assert 'mwapi:search "Platão"' in query
    assert 'mwapi:search "He said \\"hi\\" \\\\ back"' in query
    assert "OPTIONAL { ?item wdt:P106 ?occ. }" in query  # P106 原值取回，客户端过滤
    assert query.count("SERVICE wikibase:mwapi") == 2


def test_build_sparql_fulltext_query() -> None:
    """第 4 兜底 pass：全文搜索块，限直挂 P106=Q49757（单姓氏短名兜底）。"""
    from fd_zhesi_reference.wikidata import build_sparql_fulltext_query

    query = build_sparql_fulltext_query("Hume", limit=15)
    assert 'wikibase:api "Search"' in query
    assert 'mwapi:srsearch "Hume haswbstatement:P106=Q49757"' in query
    assert 'mwapi:srnamespace "0"' in query
    assert "?item wikibase:apiOutputItem mwapi:title." in query
    assert 'BIND("Hume" AS ?name)' in query
    # 批量版：fulltext=True 走同样的 Search 块
    batched = build_sparql_batch_query(["Hume", "Montaigne"], fulltext=True, limit=15)
    assert batched.count('wikibase:api "Search"') == 2
    assert batched.count("SERVICE wikibase:mwapi") == 2
    assert "UNION" in batched


def test_parse_sparql_batch_groups_rows() -> None:
    per_name = parse_sparql_batch(SPARQL_BATCH_SAMPLE)
    assert set(per_name) == {"Tales de Mileto"}
    cands = per_name["Tales de Mileto"]
    thales = cands["Q41763"]
    assert thales.label_pt == "Tales de Mileto"
    assert set(thales.aliases_en) == {"Thales", "Thales the Athenian"}  # 逗号拼接拆分
    assert thales.births == (-626, -624)  # 两个 P569 值跨行合并
    assert thales.deaths == (-547,)
    assert thales.occupations == ("Q49757", "Q4964182")  # 多行职业合并（含本体洞重复项）
    assert cands["Q999001"].label_en == "Thales of Athens"
    # 干扰项被年份错配压过：Q41763 胜出
    atlas = _atlas(atlas_id=1, name="Tales de Mileto", birth_year=-625, death_year=-545)
    best = choose_best(atlas, list(cands.values()))
    assert best is not None and best.candidate.qid == "Q41763"


def test_label_service_qid_fallback_cleaned() -> None:
    """label service 的坑：无该语言标签时回填 QID 字符串——解析层必须清洗为 None。"""
    from fd_zhesi_reference.wikidata import parse_sparql_batch

    data = {
        "head": {"vars": ["name", "item", "labelPt", "labelEn", "labelZh", "altPt", "altEn", "occ", "birth", "death"]},
        "results": {"bindings": [
            {
                "name": {"type": "literal", "value": "Hassan Hanafi"},
                "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q1112049"},
                "labelPt": {"type": "literal", "value": "Hasan Hanafi"},
                "labelEn": {"type": "literal", "value": "Hasan Hanafi"},
                "labelZh": {"type": "literal", "value": "Q1112049"},  # 无 zh 标签 → 回填 QID
            },
        ]},
    }
    cand = parse_sparql_batch(data)["Hassan Hanafi"]["Q1112049"]
    assert cand.label_zh is None  # 不是 "Q1112049"
    assert cand.label_pt == "Hasan Hanafi" and cand.label_en == "Hasan Hanafi"


def test_parse_sparql_closure() -> None:
    closure = parse_sparql_closure(SPARQL_CLOSURE_SAMPLE)
    assert closure == {"Q49757", "Q41775", "Q123456"}  # Q49757 自身始终在


# ------------------------------------------------- 实体详情 / 职业标签小查询

ENTITY_DETAILS_SAMPLE = {
    "head": {"vars": ["item", "labelPt", "labelEn", "labelZh", "altPt", "altEn", "occ", "birth", "death"]},
    "results": {"bindings": [
        {
            "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q36303"},
            "labelPt": {"type": "literal", "xml:lang": "pt", "value": "Tales de Mileto"},
            "labelEn": {"type": "literal", "xml:lang": "en", "value": "Thales"},
            "labelZh": {"type": "literal", "xml:lang": "zh", "value": "泰勒斯"},
            "occ": {"type": "uri", "value": "http://www.wikidata.org/entity/Q4964182"},
            "birth": {"type": "literal", "value": "-0624-01-01T00:00:00Z"},
            "death": {"type": "literal", "value": "-0545-01-01T00:00:00Z"},
        },
        {  # 同实体第二行：另一个 P106 值
            "item": {"type": "uri", "value": "http://www.wikidata.org/entity/Q36303"},
            "occ": {"type": "uri", "value": "http://www.wikidata.org/entity/Q170790"},
        },
    ]},
}

OCC_LABELS_SAMPLE = {
    "head": {"vars": ["occ", "occLabel"]},
    "results": {"bindings": [
        {"occ": {"type": "uri", "value": "http://www.wikidata.org/entity/Q4964182"},
         "occLabel": {"type": "literal", "xml:lang": "en", "value": "philosopher"}},
        {"occ": {"type": "uri", "value": "http://www.wikidata.org/entity/Q4964182"},
         "occLabel": {"type": "literal", "xml:lang": "pt", "value": "filósofo"}},
        {"occ": {"type": "uri", "value": "http://www.wikidata.org/entity/Q170790"},
         "occLabel": {"type": "literal", "xml:lang": "en", "value": "mathematician"}},
        {"occ": {"type": "uri", "value": "http://www.wikidata.org/entity/Q88208585"},
         "occLabel": {"type": "literal", "xml:lang": "en", "value": "natural philosopher"}},
    ]},
}


def test_entity_details_query_and_parse() -> None:
    from fd_zhesi_reference.wikidata import build_entity_details_query, parse_sparql_entities

    query = build_entity_details_query(["Q36303", "Q41155"])
    assert "VALUES ?item { wd:Q36303 wd:Q41155 }" in query
    assert "OPTIONAL { ?item wdt:P106 ?occ. }" in query

    entities = parse_sparql_entities(ENTITY_DETAILS_SAMPLE)
    thales = entities["Q36303"]
    assert thales.label_pt == "Tales de Mileto" and thales.label_zh == "泰勒斯"
    assert thales.births == (-624,) and thales.deaths == (-545,)
    assert thales.occupations == ("Q4964182", "Q170790")  # 跨行合并


def test_occ_labels_query_and_parse() -> None:
    from fd_zhesi_reference.wikidata import build_occ_labels_query, parse_occ_labels

    query = build_occ_labels_query(["Q4964182", "Q170790"])
    assert "VALUES ?occ { wd:Q4964182 wd:Q170790 }" in query

    phil = parse_occ_labels(OCC_LABELS_SAMPLE)
    assert phil == {"Q4964182", "Q88208585"}  # mathematician 不入


# ---------------------------------------------------------------- 工件汇编


def test_build_alignment_end_to_end() -> None:
    rows = load_atlas_philosophers(ATLAS_CSV)
    candidates = {
        1: [candidate_from_entity("Q41763", ENTITY_THALES)],
        2: [candidate_from_entity("Q43499", ENTITY_ERASMUS)],
        3: [candidate_from_entity("Q937", ENTITY_KANT)],
        4: [Candidate(qid="Q777", label_en="Someone Else", births=(1500,))],  # 证据不足
    }
    artifact = build_alignment(
        rows, candidates, transport="wikidata-api", occupation_filter="closure",
        generated_at="2026-10-20T00:00:00+00:00",
    )
    assert artifact["meta"]["source"] == "wikidata"
    assert artifact["meta"]["license"] == "CC0-1.0"
    aligned = {row["atlas_id"]: row for row in artifact["aligned"]}
    assert set(aligned) == {1, 2, 3}
    assert aligned[1]["qid"] == "Q41763"
    assert aligned[2]["name_en"] == "Erasmus"
    assert aligned[2]["name_zh"] == "德西德里乌斯·伊拉斯谟"
    assert aligned[1]["name_zh"] is None  # zh 缺失置 NULL
    assert aligned[3]["confidence"] in ("high", "medium")
    assert {row["atlas_id"] for row in artifact["unmatched"]} == {4}
    assert artifact["unmatched"][0]["reason"].startswith("below_threshold")

    stats = artifact["stats"]
    assert stats["total"] == 4 and stats["aligned"] == 3 and stats["rate"] == 0.75
    assert stats["name_en_filled"] == 3 and stats["name_zh_filled"] == 2
    assert sum(stats["confidence"].values()) == 3


def test_build_alignment_skipped_names_honest_reason() -> None:
    """传输层跳过的名字：如实记 sparql_query_failed，不冒充"无此人物"。"""
    rows = load_atlas_philosophers(ATLAS_CSV)
    artifact = build_alignment(
        rows,
        {1: [], 2: [], 3: [], 4: []},
        transport="sparqlwrapper",
        occupation_filter="closure",
        generated_at="2026-10-20T00:00:00+00:00",
        skipped_names={"Tales de Mileto"},
    )
    reasons = {m["atlas_id"]: m["reason"] for m in artifact["unmatched"]}
    assert reasons[1].startswith("sparql_query_failed(no_candidates)")
    assert reasons[2] == "no_candidates"  # 未被跳过的名字不带该前缀
