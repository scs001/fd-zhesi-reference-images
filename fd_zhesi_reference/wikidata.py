"""Wikidata 对齐纯函数库（task 2.3）：Atlas 人物 → Wikidata QID。

只做计算与解析，无网络、无 IO、无 DB——网络编排与落库在 scripts/ingest_wikidata.py。

数据面：
- Atlas（CC BY 4.0）：376 人，name 为葡语形式（如 "Tales de Mileto"），年份可为负。
- Wikidata（CC0）：职业 P106=Q49757（哲学家）或其子类；标签 pt/en/zh、别名、P569/P570。

打分口径（满分 80）：
- 名字证据取最大值，不叠加：pt 标签精确 40 / en 标签精确 35 / 别名精确 35 / 包含 15
- 生年 ±2 命中 +20，卒年 ±2 命中 +20（多值时任一值命中即算）
- 生（卒）年双向都有值且最小差 > 30 记 -15（排掉同人异代的错配；
  上古人物生卒的学派分歧常达一二十年，留在不奖不罚的灰区）
- 得分 ≥ 35 视为对齐：confidence high ≥ 70 / medium ≥ 50 / low ≥ 35

职业口径：P106 ∈ Q49757 子类闭包（SPARQL 取，共 ~134 项）。注意 Wikidata
本体有洞：多数上古哲人挂的是重复项（如 Q4964182 label 就是 "philosopher"，
却不是 Q49757 的 P279 子类），因此闭包之外再用职业/描述的措辞信号兜底。
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass, field

# ---- 打分常量（测试锚点；调整需同步更新 docstring 与测试）----
SCORE_PT_LABEL_EXACT = 40
SCORE_EN_LABEL_EXACT = 35
SCORE_ALIAS_EXACT = 35
SCORE_CONTAINS = 15
SCORE_YEAR_MATCH = 20
PENALTY_YEAR_MISMATCH = -15
SCORE_THRESHOLD = 35
YEAR_TOLERANCE = 2
YEAR_MISMATCH = 30
CONFIDENCE_HIGH = 70
CONFIDENCE_MEDIUM = 50

PHILOSOPHER_QID = "Q49757"


@dataclass
class AtlasPhilosopher:
    """Atlas philosophers.csv 的一行（对齐输入）。"""

    atlas_id: int
    name: str
    birth_year: int | None = None
    death_year: int | None = None


@dataclass
class Candidate:
    """一个 Wikidata 候选实体（已按名字搜出，职业过滤另做）。"""

    qid: str
    label_pt: str | None = None
    label_en: str | None = None
    label_zh: str | None = None
    aliases_pt: tuple[str, ...] = ()
    aliases_en: tuple[str, ...] = ()
    births: tuple[int, ...] = ()  # P569 全部非弃用值的年份
    deaths: tuple[int, ...] = ()  # P570 同上
    occupations: tuple[str, ...] = ()  # P106 全部 QID
    occupation_ok: bool = True  # 是否通过 P106=Q49757 子类闭包（或服务端过滤）
    descriptions: tuple[str, ...] = ()  # (en/pt) 描述，闭包不可用时的降级职业信号

    def display_name_en(self) -> str | None:
        """en 标签，缺失时用首个 en 别名兜底；都没有返回 None（不硬造）。"""
        return self.label_en or (self.aliases_en[0] if self.aliases_en else None)


@dataclass
class Scored:
    """打分结果；method 描述证据构成（如 pt_label_exact+birth_year+death_year）。"""

    candidate: Candidate
    score: int
    reasons: list[str] = field(default_factory=list)

    @property
    def confidence(self) -> str:
        if self.score >= CONFIDENCE_HIGH:
            return "high"
        if self.score >= CONFIDENCE_MEDIUM:
            return "medium"
        return "low"

    @property
    def method(self) -> str:
        return "+".join(self.reasons) if self.reasons else "no_evidence"


# ---------------------------------------------------------------- 基础归一


def strip_accents(text: str) -> str:
    """去变音号：NFD 后丢弃组合附加符号（Roterdã → Roterda）。"""
    return "".join(
        ch for ch in unicodedata.normalize("NFD", text) if not unicodedata.combining(ch)
    )


def normalize_name(text: str) -> str:
    """比较用规范形：去重音、小写、连字符/撇号转空格、压缩空白。"""
    if not text:
        return ""
    s = strip_accents(text).casefold()
    s = re.sub(r"['’\-–—_]+", " ", s)  # Jean-Jacques → jean jacques
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    return re.sub(r"\s+", " ", s).strip()


def sparql_time_to_year(value: str | None) -> int | None:
    """Wikidata 时间字面量取年：'-0624-00-00T00:00:00Z' → -624；非法返回 None。"""
    if not value:
        return None
    m = re.match(r"^([+-]?\d{1,6})-\d{2}-\d{2}", str(value))
    if not m:
        return None
    year = int(m.group(1))
    return year if year != 0 else None


def _int_or_none(raw: str | None) -> int | None:
    if raw is None or not str(raw).strip():
        return None
    return int(float(str(raw).strip()))  # 源文件混有 "-545.0" 风格年份


def load_atlas_philosophers(csv_text: str) -> list[AtlasPhilosopher]:
    """解析 Atlas philosophers.csv（表头 id,name,birth_year,death_year,...）。"""
    out: list[AtlasPhilosopher] = []
    for row in csv.DictReader(io.StringIO(csv_text)):
        name = (row.get("name") or "").strip()
        if not name or not (row.get("id") or "").strip():
            continue
        out.append(
            AtlasPhilosopher(
                atlas_id=int(row["id"]),
                name=name,
                birth_year=_int_or_none(row.get("birth_year")),
                death_year=_int_or_none(row.get("death_year")),
            )
        )
    if not out:
        raise ValueError("philosophers.csv 解析结果为空（表头或数据不符）")
    return out


# ------------------------------------------------------- Wikidata 实体解析


_ZH_LABEL_PRIORITY = ("zh", "zh-hans", "zh-hant")


def candidate_from_entity(qid: str, entity: dict) -> Candidate:
    """wbgetentities 的单个实体 JSON → Candidate。

    labels/aliases 按 pt / pt-br / en / zh 系归类；P569/P570 取非弃用值；
    缺失节（重定向空壳等）产出占位 Candidate（仅 qid）。
    """
    labels = entity.get("labels") or {}
    aliases = entity.get("aliases") or {}
    descriptions = entity.get("descriptions") or {}
    claims = entity.get("claims") or {}

    def _lang(d: dict, *langs: str) -> str | None:
        for lang in langs:
            hit = d.get(lang)
            if hit and hit.get("value"):
                return hit["value"]
        return None

    def _alias_tuple(d: dict, *langs: str) -> tuple[str, ...]:
        out: list[str] = []
        for lang in langs:
            for item in d.get(lang) or []:
                if item.get("value"):
                    out.append(item["value"])
        return tuple(dict.fromkeys(out))  # 去重保序

    def _claim_years(prop: str) -> tuple[int, ...]:
        years: list[int] = []
        for claim in claims.get(prop) or []:
            if claim.get("rank") == "deprecated":
                continue
            snak = claim.get("mainsnak") or {}
            if snak.get("snaktype") != "value":
                continue
            value = (snak.get("datavalue") or {}).get("value") or {}
            year = sparql_time_to_year(value.get("time") if isinstance(value, dict) else None)
            if year is not None:
                years.append(year)
        return tuple(dict.fromkeys(years))

    def _claim_qids(prop: str) -> tuple[str, ...]:
        qids: list[str] = []
        for claim in claims.get(prop) or []:
            snak = claim.get("mainsnak") or {}
            if snak.get("snaktype") != "value":
                continue
            value = (snak.get("datavalue") or {}).get("value") or {}
            if isinstance(value, dict) and value.get("id"):
                qids.append(value["id"])
        return tuple(dict.fromkeys(qids))

    desc_parts = [
        d["value"]
        for d in (descriptions.get(lang) for lang in ("en", "pt", "pt-br"))
        if d and d.get("value")
    ]
    return Candidate(
        qid=entity.get("id") or qid,  # 重定向后以实体自身 id 为准
        label_pt=_lang(labels, "pt", "pt-br"),
        label_en=_lang(labels, "en"),
        label_zh=_lang(labels, *_ZH_LABEL_PRIORITY),
        aliases_pt=_alias_tuple(aliases, "pt", "pt-br"),
        aliases_en=_alias_tuple(aliases, "en"),
        births=_claim_years("P569"),
        deaths=_claim_years("P570"),
        occupations=_claim_qids("P106"),
        descriptions=tuple(desc_parts),
    )


def parse_wbsearch(wbsearch_json: dict) -> list[str]:
    """wbsearchentities 响应 → 候选 QID 列表（保序去重；截断于 API limit）。"""
    qids: list[str] = []
    for hit in wbsearch_json.get("search") or []:
        qid = hit.get("id")
        if qid and qid.startswith("Q") and qid[1:].isdigit():
            qids.append(qid)
    return list(dict.fromkeys(qids))


def occupation_label_is_philosopher(labels: list[str]) -> bool:
    """职业标签措辞判定：philosopher / filósofo / pre-Socratic philosopher 等算数。

    Wikidata 本体洞的兜底：Q4964182 的 label 就是 "philosopher"，却不是
    Q49757 的 P279 子类——闭包会漏掉它，标签措辞不会。
    """
    blob = " ".join(labels).casefold()
    return "philosop" in blob or "filósofo" in blob or "filosof" in blob


def apply_occupation_filter(
    candidates: list[Candidate],
    closure: set[str] | None,
    philosopher_occupations: set[str] | None = None,
) -> tuple[list[Candidate], str]:
    """候选的职业过滤（三信号，任一命中即哲学家口径）：

    1. P106 ∈ Q49757 子类闭包（closure，SPARQL 取）；
    2. P106 ∈ philosopher_occupations（职业标签措辞为 philosopher 的 QID 集，
       如重复项 Q4964182）；
    3. 实体描述含 philosopher/filósofo。

    closure 给定：三信号全不中的候选直接剔除（严格口径）。
    closure 为 None（闭包没拿到）：无法严格过滤，全保留但仅命中信号的
    occupation_ok=True，其余标 False（打分时 method 附注 occupation_unverified）。
    """
    phil_occs = philosopher_occupations or set()
    strict = closure is not None

    def _ok(c: Candidate) -> bool:
        if PHILOSOPHER_QID in c.occupations:
            return True
        if closure and any(q in closure for q in c.occupations):
            return True
        if any(q in phil_occs for q in c.occupations):
            return True
        return occupation_label_is_philosopher(list(c.descriptions))

    kept: list[Candidate] = []
    for c in candidates:
        if _ok(c):
            c.occupation_ok = True
            kept.append(c)
        elif not strict:
            c.occupation_ok = False
            kept.append(c)
    return kept, "closure" if strict else "loose"


# ------------------------------------------------------------- SPARQL 通道


def escape_sparql_string(text: str) -> str:
    """SPARQL 字符串字面量转义（CSV 名字不含引号/反斜杠，双保险）。"""
    return text.replace("\\", "\\\\").replace('"', '\\"')


def build_sparql_closure_query() -> str:
    """Q49757（哲学家）全部子类：小查询，服务中断期也扛得住（实测 ~1s）。"""
    return "SELECT ?occ WHERE { ?occ wdt:P279* wd:Q49757. }"


def parse_sparql_closure(sparql_json: dict) -> set[str]:
    closure = {PHILOSOPHER_QID}
    for binding in sparql_json.get("results", {}).get("bindings") or []:
        uri = (binding.get("occ") or {}).get("value") or ""
        qid = uri.rsplit("/", 1)[-1]
        if re.fullmatch(r"Q\d+", qid):
            closure.add(qid)
    return closure


def _mwapi_block(name: str, *, lang: str = "pt", limit: int = 8, fulltext: bool = False) -> str:
    """单个候选搜索服务块：EntitySearch（前缀）或 Search（全文）。

    不在服务端做 P106 过滤——Wikidata 本体有洞（多数上古哲人的 P106 挂在
    重复职业项上，不是 Q49757 的子类），服务端滤会把泰勒斯们全滤掉；
    改取 P106 原值（?occ），客户端三信号过滤。
    全文块限 haswbstatement:P106=Q49757（单姓氏短名兜底，见 build_sparql_fulltext_query）。
    """
    esc = escape_sparql_string(name)
    if fulltext:
        service = f"""      mwapi:srsearch "{esc} haswbstatement:P106=Q49757";
                      mwapi:srlimit "{limit}";
                      mwapi:srnamespace "0".
      ?item wikibase:apiOutputItem mwapi:title."""
    else:
        service = f"""      mwapi:search "{esc}";
                      mwapi:language "{lang}";
                      mwapi:limit "{limit}".
      ?item wikibase:apiOutputItem mwapi:item."""
    return f"""  {{
    BIND("{esc}" AS ?name)
    SERVICE wikibase:mwapi {{
      bd:serviceParam wikibase:endpoint "www.wikidata.org";
                      wikibase:api "{"Search" if fulltext else "EntitySearch"}";
{service}
    }}
  }}"""


def build_sparql_batch_query(
    names: list[str], lang: str = "pt", limit: int = 8, fulltext: bool = False
) -> str:
    """批量候选查询：N 个名字 UNION N 个 mwapi 搜索服务块 + label service。"""
    blocks = [_mwapi_block(n, lang=lang, limit=limit, fulltext=fulltext) for n in names]
    union = "\n  UNION\n".join(blocks)
    return f"""SELECT ?name ?item ?labelPt ?labelEn ?labelZh ?altPt ?altEn ?occ ?birth ?death WHERE {{
{union}
  OPTIONAL {{ ?item wdt:P106 ?occ. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "pt,pt-br". ?item rdfs:label ?labelPt. ?item skos:altLabel ?altPt. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". ?item rdfs:label ?labelEn. ?item skos:altLabel ?altEn. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "zh,zh-hans,zh-hant". ?item rdfs:label ?labelZh. }}
  OPTIONAL {{ ?item wdt:P569 ?birth. }}
  OPTIONAL {{ ?item wdt:P570 ?death. }}
}}"""


def build_sparql_fulltext_query(name: str, limit: int = 15) -> str:
    """第 4 兜底 pass（单人版）：CirrusSearch 全文搜索，限定直挂 P106=Q49757。

    EntitySearch 是前缀匹配，单姓氏短名（"Hume" 召不回 pt 标签 "David Hume"
    的休谟）系统性漏召回；全文搜索按 token 命中任意位置。职业限定用
    haswbstatement 直挂口径（本 pass 目标是现代哲学家，直挂覆盖足够；
    上古本体洞人物由前三个 EntitySearch pass 负责）。
    """
    return build_sparql_batch_query([name], limit=limit, fulltext=True)


def build_sparql_fulltext_query(name: str, limit: int = 15) -> str:
    """第 4 兜底 pass：CirrusSearch 全文搜索（限定直挂 P106=Q49757 的实体）。

    EntitySearch 是前缀匹配，单姓氏短名（"Hume" 召不回 pt 标签 "David Hume"
    的休谟）系统性漏召回；全文搜索按 token 命中任意位置。职业限定用
    haswbstatement 直挂口径（本 pass 目标是现代哲学家，直挂覆盖足够；
    上古本体洞人物由前三个 EntitySearch pass 负责）。
    """
    esc = escape_sparql_string(name)
    return f"""SELECT ?name ?item ?labelPt ?labelEn ?labelZh ?altPt ?altEn ?occ ?birth ?death WHERE {{
  BIND("{esc}" AS ?name)
  SERVICE wikibase:mwapi {{
    bd:serviceParam wikibase:endpoint "www.wikidata.org";
                    wikibase:api "Search";
                    mwapi:srsearch "{esc} haswbstatement:P106=Q49757";
                    mwapi:srlimit "{limit}";
                    mwapi:srnamespace "0".
    ?item wikibase:apiOutputItem mwapi:title.
  }}
  OPTIONAL {{ ?item wdt:P106 ?occ. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "pt,pt-br". ?item rdfs:label ?labelPt. ?item skos:altLabel ?altPt. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". ?item rdfs:label ?labelEn. ?item skos:altLabel ?altEn. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "zh,zh-hans,zh-hant". ?item rdfs:label ?labelZh. }}
  OPTIONAL {{ ?item wdt:P569 ?birth. }}
  OPTIONAL {{ ?item wdt:P570 ?death. }}
}}"""


def batch_fulltext_queries(names: list[str], limit: int = 15) -> str:
    """多名的全文搜索 UNION 批（每名独立 mwapi Search 服务块）。"""
    blocks = [build_sparql_fulltext_query(n, limit) for n in names]
    return "\n  UNION\n".join(blocks)


def parse_sparql_batch(sparql_json: dict) -> dict[str, dict[str, Candidate]]:
    """批量 SPARQL 响应 → {搜索名: {qid: Candidate}}。

    同一 (name, item) 的多行（多别名/多职业/多生卒值）合并进同一 Candidate。
    SPARQLWrapper(JSON) 的返回与 sparql-results+json 同构，此处只认绑定结构。
    """
    out: dict[str, dict[str, Candidate]] = {}
    for row in sparql_json.get("results", {}).get("bindings") or []:
        name = (row.get("name") or {}).get("value")
        if not name:
            continue
        per_name = out.setdefault(name, {})
        _merge_binding_row(per_name, row)
    return out


def _clean_label(value: str | None) -> str | None:
    """label service 的坑：无该语言标签时它会回填实体 QID 字符串——视为无标签。"""
    if value is None:
        return None
    return None if re.fullmatch(r"Q\d+", value) else value


def _merge_binding_row(per_name: dict[str, Candidate], row: dict) -> None:
    """一行绑定（?item + label service 输出 + occ/birth/death）并入候选表。"""
    qid = ((row.get("item") or {}).get("value") or "").rsplit("/", 1)[-1]
    if not re.fullmatch(r"Q\d+", qid):
        return
    cand = per_name.get(qid)
    if cand is None:
        cand = Candidate(qid=qid)
        per_name[qid] = cand

    def _val(key: str) -> str | None:
        binding = row.get(key)
        return binding.get("value") if binding else None

    if label_pt := _clean_label(_val("labelPt")):
        cand.label_pt = cand.label_pt or label_pt
    if label_en := _clean_label(_val("labelEn")):
        cand.label_en = cand.label_en or label_en
    if label_zh := _clean_label(_val("labelZh")):
        cand.label_zh = cand.label_zh or label_zh
    # label service 的别名输出是逗号拼接；别名本身含逗号的会被切碎（可接受损失）
    for blob, attr in ((_val("altPt"), "aliases_pt"), (_val("altEn"), "aliases_en")):
        if blob:
            parts = tuple(p.strip() for p in blob.split(",") if p.strip())
            merged = tuple(dict.fromkeys(getattr(cand, attr) + parts))
            setattr(cand, attr, merged)
    if (occ := _val("occ")) and re.fullmatch(r"Q\d+", occ.rsplit("/", 1)[-1]):
        occ_qid = occ.rsplit("/", 1)[-1]
        cand.occupations = tuple(dict.fromkeys(cand.occupations + (occ_qid,)))
    if (birth := sparql_time_to_year(_val("birth"))) is not None:
        cand.births = tuple(dict.fromkeys(cand.births + (birth,)))
    if (death := sparql_time_to_year(_val("death"))) is not None:
        cand.deaths = tuple(dict.fromkeys(cand.deaths + (death,)))


def build_entity_details_query(qids: list[str]) -> str:
    """VALUES 小查询：批量取实体详情（pt/en/zh 标签、别名、P106/P569/P570）。

    用于把缓存里 wbsearch 召回的裸 QID 列表补成可打分的完整候选。
    """
    values = " ".join(f"wd:{q}" for q in qids)
    return f"""SELECT ?item ?labelPt ?labelEn ?labelZh ?altPt ?altEn ?occ ?birth ?death WHERE {{
  VALUES ?item {{ {values} }}
  OPTIONAL {{ ?item wdt:P106 ?occ. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "pt,pt-br". ?item rdfs:label ?labelPt. ?item skos:altLabel ?altPt. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en". ?item rdfs:label ?labelEn. ?item skos:altLabel ?altEn. }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "zh,zh-hans,zh-hant". ?item rdfs:label ?labelZh. }}
  OPTIONAL {{ ?item wdt:P569 ?birth. }}
  OPTIONAL {{ ?item wdt:P570 ?death. }}
}}"""


def parse_sparql_entities(sparql_json: dict) -> dict[str, Candidate]:
    """实体详情查询（build_entity_details_query）的响应 → {qid: Candidate}。"""
    per_qid: dict[str, Candidate] = {}
    for row in sparql_json.get("results", {}).get("bindings") or []:
        if not row.get("item"):
            continue
        _merge_binding_row(per_qid, row)
    return per_qid


def build_occ_labels_query(qids: list[str]) -> str:
    """VALUES 小查询：职业项的 en/pt 标签（本体洞兜底用）。"""
    values = " ".join(f"wd:{q}" for q in qids)
    return f"""SELECT ?occ ?occLabel WHERE {{
  VALUES ?occ {{ {values} }}
  ?occ rdfs:label ?occLabel.
  FILTER(LANG(?occLabel) IN ("en", "pt", "pt-br"))
}}"""


def parse_occ_labels(sparql_json: dict) -> set[str]:
    """职业标签查询响应 → 措辞为 philosopher 系的职业 QID 集。"""
    phil: set[str] = set()
    labels_by_qid: dict[str, list[str]] = {}
    for row in sparql_json.get("results", {}).get("bindings") or []:
        qid = ((row.get("occ") or {}).get("value") or "").rsplit("/", 1)[-1]
        label = (row.get("occLabel") or {}).get("value")
        if qid and label:
            labels_by_qid.setdefault(qid, []).append(label)
    for qid, labels in labels_by_qid.items():
        if occupation_label_is_philosopher(labels):
            phil.add(qid)
    return phil


# ----------------------------------------------------------------- 打分


def _years_relation(atlas_year: int | None, cand_years: tuple[int, ...]) -> str:
    """单边年份关系：match（±YEAR_TOLERANCE 任一值命中）/ mismatch（都有值且最小差超限）/ none。"""
    if atlas_year is None or not cand_years:
        return "none"
    diffs = [abs(cand - atlas_year) for cand in cand_years]
    if min(diffs) <= YEAR_TOLERANCE:
        return "match"
    if min(diffs) > YEAR_MISMATCH:
        return "mismatch"
    return "none"  # 3~15 年的学术分歧区：不奖不罚


def _tokens_match(a: str, b: str) -> bool:
    """token 级模糊等值：全等，或（≥4 字符时）一方是另一方的前缀。

    覆盖 "Kant"→"Immanuel Kant"（token 子集）与 "Roterdã"→"Roterdão"
    （尾音节差）这类葡语形式的系统性偏差。
    """
    if a == b:
        return True
    return len(a) >= 4 and len(b) >= 4 and (a.startswith(b) or b.startswith(a))


def _token_set_contains(smaller: list[str], larger: list[str]) -> bool:
    """smaller 的每个 token 都能在 larger 里找到模糊对应。"""
    return all(any(_tokens_match(tok, other) for other in larger) for tok in smaller)


def _name_contains(target: str, label: str) -> bool:
    """整串前缀或 token 集包含（"kant" ⊂ "immanuel kant"；"roterda"~"roterdao"）。"""
    if not target or not label or len(target) < 4:
        return False
    if label.startswith(target) or target.startswith(label):
        return True
    t_tokens = target.split()
    l_tokens = label.split()
    return _token_set_contains(t_tokens, l_tokens) or _token_set_contains(l_tokens, t_tokens)


def score_candidate(atlas: AtlasPhilosopher, cand: Candidate) -> Scored:
    """一个候选的打分：名字证据取最大、生卒年加分、错配减分。"""
    target = normalize_name(atlas.name)
    reasons: list[str] = []

    name_score = 0
    name_reason = None
    if target:
        label_pt = normalize_name(cand.label_pt or "")
        label_en = normalize_name(cand.label_en or "")
        alias_pt = {normalize_name(a) for a in cand.aliases_pt}
        alias_en = {normalize_name(a) for a in cand.aliases_en}
        if label_pt and label_pt == target:
            name_score, name_reason = SCORE_PT_LABEL_EXACT, "pt_label_exact"
        elif label_en and label_en == target:
            name_score, name_reason = SCORE_EN_LABEL_EXACT, "en_label_exact"
        elif target in alias_pt | alias_en:
            name_score, name_reason = SCORE_ALIAS_EXACT, "alias_exact"
        elif _name_contains(target, label_pt) or _name_contains(target, label_en):
            name_score, name_reason = SCORE_CONTAINS, "contains"
    if name_reason:
        reasons.append(name_reason)

    score = name_score
    for prop, atlas_year, cand_years in (
        ("birth_year", atlas.birth_year, cand.births),
        ("death_year", atlas.death_year, cand.deaths),
    ):
        relation = _years_relation(atlas_year, cand_years)
        if relation == "match":
            score += SCORE_YEAR_MATCH
            reasons.append(prop)
        elif relation == "mismatch":
            score += PENALTY_YEAR_MISMATCH
            reasons.append(f"{prop}_mismatch")

    if not cand.occupation_ok:
        reasons.append("occupation_unverified")

    return Scored(candidate=cand, score=score, reasons=reasons)


def choose_best(atlas: AtlasPhilosopher, candidates: list[Candidate]) -> Scored | None:
    """最高分候选（≥ SCORE_THRESHOLD），确定性排序：分数降序、QID 升序。"""
    scored = [score_candidate(atlas, c) for c in candidates]
    scored.sort(key=lambda s: (-s.score, s.candidate.qid))
    best = scored[0] if scored else None
    if best is None or best.score < SCORE_THRESHOLD:
        return None
    return best


def unaligned_reason(atlas: AtlasPhilosopher, candidates: list[Candidate]) -> str:
    """未对齐原因：搜索没召回 vs 召回了但证据不足（附最高分便于诊断）。"""
    if not candidates:
        return "no_candidates"
    best = max((score_candidate(atlas, c) for c in candidates), key=lambda s: s.score)
    return f"below_threshold(best={best.score},method={best.method})"


# --------------------------------------------------------------- 汇编产出


def build_alignment(
    rows: list[AtlasPhilosopher],
    candidates_by_atlas_id: dict[int, list[Candidate]],
    *,
    transport: str,
    occupation_filter: str,
    generated_at: str,
    skipped_names: set[str] | None = None,
) -> dict:
    """产出权威工件结构：aligned / unmatched / stats（scripts/artifacts/ 的 JSON 形态）。

    skipped_names：传输层短退避后跳过、未能活查询召回的名字——如实记入
    未对齐原因（sparql_query_failed），不冒充"无此人物"。
    """
    aligned: list[dict] = []
    unmatched: list[dict] = []
    confidence_counts = {"high": 0, "medium": 0, "low": 0}
    name_en_filled = 0
    name_zh_filled = 0
    skipped_names = skipped_names or set()

    for row in rows:
        candidates = candidates_by_atlas_id.get(row.atlas_id, [])
        best = choose_best(row, candidates)
        if best is None:
            reason = unaligned_reason(row, candidates)
            if row.name in skipped_names:
                reason = f"sparql_query_failed({reason})"
            unmatched.append(
                {"atlas_id": row.atlas_id, "name_pt": row.name, "reason": reason}
            )
            continue
        cand = best.candidate
        name_en = cand.display_name_en()
        confidence_counts[best.confidence] += 1
        name_en_filled += bool(name_en)
        name_zh_filled += bool(cand.label_zh)
        aligned.append(
            {
                "atlas_id": row.atlas_id,
                "name_pt": row.name,
                "qid": cand.qid,
                "name_en": name_en,
                "name_zh": cand.label_zh,  # zh 标签缺失置 NULL，不硬翻
                "score": best.score,
                "confidence": best.confidence,
                "method": best.method,
            }
        )

    total = len(rows)
    return {
        "meta": {
            "source": "wikidata",
            "license": "CC0-1.0",
            "generated_at": generated_at,
            "transport": transport,  # sparql-batch / wikidata-api（WDQS 中断期的降级通道）
            "occupation_filter": occupation_filter,  # closure（SPARQL 闭包+标签措辞）/ loose（闭包缺失）
            "score_threshold": SCORE_THRESHOLD,
            "note": "Atlas 种子（CC BY 4.0，见 docs/data-license.md）与 Wikidata 的实体对齐工件；"
            "DB 回填以本文件为准，可重复 apply",
        },
        "aligned": aligned,
        "unmatched": unmatched,
        "stats": {
            "total": total,
            "aligned": len(aligned),
            "unmatched": len(unmatched),
            "rate": round(len(aligned) / total, 4) if total else 0.0,
            "confidence": confidence_counts,
            "name_en_filled": name_en_filled,
            "name_zh_filled": name_zh_filled,
        },
    }
