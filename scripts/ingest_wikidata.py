"""Wikidata 摄取器（task 2.3）：Atlas 376 人 → Wikidata QID 对齐 + name_en/name_zh 回填。

SPARQL 访问统一走开源库 SPARQLWrapper（格式协商/绑定结构/UA/超时库自带，
家族规矩：不手搓传输层）。查询形态全部是 SPARQL：

  子类闭包   SELECT ?occ WHERE { ?occ wdt:P279* wd:Q49757. }
  候选搜索   wikibase:mwapi EntitySearch UNION 批（按名字，pt → en → 去重音@en 三兜底）
  实体详情   VALUES 批量取标签/别名/P106/P569/P570（补缓存里 wbsearch 的裸 QID）
  职业标签   VALUES 取 P106 职业项的 en/pt 标签（本体洞兜底）

对齐打分在 fd_zhesi_reference/wikidata.py（纯函数，离线可测）：生卒年 ±2、
pt label/altLabel 命中加权。

失败口径：短退避（≤10s）重试 2 次，仍失败就跳过该批名字，未对齐清单如实记录
（reason=sparql_query_failed），不卡进度、不长 sleep。

缓存优先：scripts/artifacts/wikidata_cache/ 里已有的响应（含早前 wbsearch/
wbgetentities 通道的缓存）先当离线输入直接解析，只对缺口发新查询。

产物：scripts/artifacts/wikidata_alignment.json（权威工件——另一任务会重插整表，
本脚本可据它重复 --apply）。

用法：
  python3 scripts/ingest_wikidata.py                    # 拉对齐（写工件，不碰 DB）
  python3 scripts/ingest_wikidata.py --apply            # 对齐结果 UPDATE 进 philosophers
  python3 scripts/ingest_wikidata.py --offline          # 只用缓存重算工件（不发请求）
  python3 scripts/ingest_wikidata.py --limit 20         # 冒烟：只跑前 20 人
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from SPARQLWrapper import JSON, POST, SPARQLWrapper  # noqa: E402  开源库：SPARQL 传输层

from fd_zhesi_reference import schema  # noqa: E402
from fd_zhesi_reference.config import Settings  # noqa: E402
from fd_zhesi_reference.wikidata import (  # noqa: E402
    Candidate,
    apply_occupation_filter,
    build_alignment,
    build_occ_labels_query,
    build_sparql_batch_query,
    build_sparql_closure_query,
    candidate_from_entity,
    load_atlas_philosophers,
    occupation_label_is_philosopher,
    parse_occ_labels,
    parse_sparql_batch,
    parse_sparql_closure,
    parse_sparql_entities,
    parse_wbsearch,
    score_candidate,
)
from fd_zhesi_reference.wikidata import SCORE_THRESHOLD  # noqa: E402

UA = "fd-zhesi-reference/0.1 (zhesi line; contact: local)"  # Wikidata UA 政策：描述性
ATLAS_CSV_PATH = "data/raw/philosophers.csv"
OCC_CHUNK = 100  # VALUES 职业标签每批 QID 数
SEARCH_LIMIT = 8  # 每名候选上限
SOURCE = "wikidata"
QUERY_GAP = 62.0  # 相邻活查询间隔：WDQS 中断期 1 req/min 硬限（任务要求的串行礼貌限速；
#                 重试退避另行短控 ≤10s ×2，查询间限速不是重试退避）


class SparqlUnavailable(RuntimeError):
    """短退避重试后仍失败的查询（调用方按名字粒度跳过并记录）。"""


class SparqlClient:
    """SPARQLWrapper 薄封装：UA/超时/短退避 ×2，返回库协商后的 JSON 绑定结构。"""

    def __init__(self, endpoint: str, timeout: int = 45):
        self.endpoint = endpoint
        self.timeout = timeout

    def query(self, query: str, *, timeout: int | None = None, tag: str = "") -> dict:
        last_error: Exception | None = None
        for attempt, backoff in enumerate((4.0, 9.0), start=1):
            try:
                sparql = SPARQLWrapper(self.endpoint)
                sparql.setQuery(query)
                sparql.setReturnFormat(JSON)
                sparql.setMethod(POST)  # 多名批查询 URL 会超请求行上限（HTTP 431），走 POST
                sparql.setTimeout(timeout or self.timeout)
                sparql.addCustomHttpHeader("User-Agent", UA)
                return sparql.queryAndConvert()
            except Exception as exc:  # noqa: BLE001 库抛的 HTTP/解析异常统一短退避
                last_error = exc
                print(f"    [sparql{('#' + tag) if tag else ''}] 第 {attempt} 次失败"
                      f"（{type(exc).__name__}: {str(exc)[:90]}）→ 等 {backoff:.0f}s 重试", flush=True)
                time.sleep(backoff)
        raise SparqlUnavailable(f"{tag or 'query'} 重试 2 次仍失败：{last_error}")


# ------------------------------------------------------------------ 缓存基座


class Cache:
    """请求级磁盘缓存：断点续跑、--offline 重算。键为稳定 hash，按文件形状回放。"""

    def __init__(self, directory: Path, refresh: bool):
        self.directory = directory
        self.refresh = refresh
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
        return self.directory / f"{digest}.json"

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if self.refresh or not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        if isinstance(payload, dict) and "error" in payload:  # 早前误缓存的错误体
            return None
        return payload

    def put(self, key: str, payload: dict) -> None:
        self._path(key).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )


# ------------------------------------------------------------- 缓存回放（离线）


def replay_cache(cache: Cache) -> dict:
    """扫描缓存文件按形状归类回放（早前 sparql 批 / wbsearch / wbgetentities 全兼容）。

    返回 {closure, candidates_by_name（sparql 批候选）, search_qids_by_name,
    entity_by_qid, phil_occs}。
    """
    closure: set[str] | None = None
    candidates_by_name: dict[str, dict[str, Candidate]] = {}
    search_qids_by_name: dict[str, list[str]] = {}
    entity_by_qid: dict[str, Candidate] = {}
    phil_occs: set[str] = set()

    for path in sorted(cache.directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "error" in data:
            continue  # 毒缓存（cirrussearch-too-busy 之类）跳过
        if "closure" in data:
            closure = set(data["closure"]) or None
        elif "text" in data:  # Atlas CSV，另行处理
            continue
        elif "search" in data and "searchinfo" in data:  # wbsearchentities 缓存
            name = (data.get("searchinfo") or {}).get("search")
            if name:
                qids = parse_wbsearch(data)
                merged = search_qids_by_name.setdefault(name, [])
                search_qids_by_name[name] = list(dict.fromkeys(merged + qids))
        elif "entities" in data:  # wbgetentities 缓存（实体详情或职业标签批）
            for req_id, ent in (data.get("entities") or {}).items():
                cand = candidate_from_entity(req_id, ent)
                if cand.occupations or cand.label_pt or cand.label_en:
                    entity_by_qid[cand.qid] = cand
                labels = [v["value"] for v in (ent.get("labels") or {}).values() if v.get("value")]
                if labels and occupation_label_is_philosopher(labels):
                    phil_occs.add(cand.qid)
        elif "results" in data and "bindings" in (data.get("results") or {}):
            bindings = data["results"]["bindings"]
            if bindings and "name" in bindings[0]:  # 候选搜索批（EntitySearch UNION）
                for name, cands in parse_sparql_batch(data).items():
                    candidates_by_name.setdefault(name, {}).update(cands)
            elif bindings and "occLabel" in bindings[0]:  # 职业标签批
                phil_occs |= parse_occ_labels(data)
            elif bindings and "item" in bindings[0]:  # 实体详情批（VALUES）
                entity_by_qid.update(parse_sparql_entities(data))
    return {
        "closure": closure,
        "candidates_by_name": candidates_by_name,
        "search_qids_by_name": search_qids_by_name,
        "entity_by_qid": entity_by_qid,
        "phil_occs": phil_occs,
    }


# ----------------------------------------------------------------- 查询编排


def _search_passes(name: str) -> list[tuple[str, str]]:
    """召回三连：原名@pt → 原名@en → 去重音@en（阿拉伯/拉丁转写名兜底）。"""
    stripped = "".join(
        ch for ch in unicodedata.normalize("NFD", name) if not unicodedata.combining(ch)
    )
    passes = [(name, "pt"), (name, "en")]
    if stripped != name:
        passes.append((stripped, "en"))
    return passes


def _run_sparql(
    client: SparqlClient | None, cache: Cache, key: str, query: str, *, tag: str = ""
) -> dict | None:
    """带缓存的查询执行：缓存命中直接回；活查询失败返回 None（调用方跳过该批）。"""
    data = cache.get(key)
    if data is not None:
        return data
    if client is None:
        return None
    time.sleep(QUERY_GAP)
    try:
        data = client.query(query, tag=tag)
    except SparqlUnavailable as exc:
        print(f"  [{tag or 'sparql'}] 跳过（{exc}）", flush=True)
        return None
    cache.put(key, data)
    return data


def _aligned(row, candidates: list[Candidate]) -> bool:
    best = max(
        (score_candidate(row, c) for c in candidates), key=lambda s: s.score, default=None
    )
    return best is not None and best.score >= SCORE_THRESHOLD


def collect_candidates(
    rows, client: SparqlClient | None, cache: Cache, replay: dict, offline: bool
) -> tuple[dict[int, list[Candidate]], set[str], bool]:
    """候选收集：缓存回放打底，缺口按需发 SPARQL 查询（失败按名字跳过）。

    返回（{atlas_id: candidates}，被跳过的名字集，闭包是否拿到）。
    """
    closure = replay["closure"]
    candidates_by_name: dict[str, dict[str, Candidate]] = dict(replay["candidates_by_name"])
    search_qids_by_name: dict[str, list[str]] = dict(replay["search_qids_by_name"])
    entity_by_qid: dict[str, Candidate] = dict(replay["entity_by_qid"])
    skipped_names: set[str] = set()

    # 1) 子类闭包（缓存没有就一次小查询；失败走 loose 口径）
    if closure is None and not offline:
        data = _run_sparql(client, cache, "sparql:closure:Q49757-v2",
                           build_sparql_closure_query(), tag="closure")
        if data is not None:
            closure = parse_sparql_closure(data)
    if closure is None:
        print("[closure] 闭包缺失 → loose 职业口径（描述措辞兜底）", flush=True)

    def snapshot() -> dict[int, list[Candidate]]:
        out: dict[int, list[Candidate]] = {}
        for row in rows:
            per: dict[str, Candidate] = {}
            for qid, cand in candidates_by_name.get(row.name, {}).items():
                per[qid] = (
                    cand if cand.label_pt or cand.label_en or cand.occupations
                    else entity_by_qid.get(qid, cand)
                )
            for qid in search_qids_by_name.get(row.name, []):
                if qid not in per and qid in entity_by_qid:
                    per[qid] = entity_by_qid[qid]
            out[row.atlas_id] = list(per.values())
        return out

    # 3) 未对齐的名字逐 pass 发候选搜索批（pt → en → 去重音@en → 全文兜底）。
    #    批里自带实体详情（label service），wbsearch 缓存召回的裸 QID 不再单发
    #    VALUES 详情查询——EntitySearch 批能召回同一批人，省一半活查询。
    #    pass 4 全文搜索兜单姓氏短名（EntitySearch 前缀匹配召不回 "David Hume" 类）。
    batch = 15
    for pass_idx in range(4):
        snap = snapshot()
        pending = [r for r in rows if not _aligned(r, snap[r.atlas_id])]
        if not pending:
            break
        print(f"[pass {pass_idx + 1}] 缺口 {len(pending)} 人（{_pass_lang(pass_idx)}）", flush=True)
        for start in range(0, len(pending), batch):
            group = pending[start : start + batch]
            if pass_idx < 3:
                searches = []
                for r in group:
                    passes = _search_passes(r.name)
                    if pass_idx < len(passes):
                        searches.append((r.name,) + passes[pass_idx])  # (原名, 搜索名, 语言)
                if not searches:
                    continue
                _, sample_name, sample_lang = searches[0]
                key = "sparql:batch2:" + "|".join(f"{lang}:{name}" for _, name, lang in searches)
                query = build_sparql_batch_query(
                    [name for _, name, _ in searches], lang=sample_lang
                )
            else:  # pass 4：全文兜底（原名，含重音）
                searches = [(r.name, r.name, "ft") for r in group]
                key = "sparql:batch-ft:" + "|".join(name for _, name, _ in searches)
                query = build_sparql_batch_query(
                    [name for _, name, _ in searches], fulltext=True, limit=15
                )
            data = _run_sparql(client, cache, key, query, tag=f"batch{pass_idx + 1}")
            if data is None:
                skipped_names.update(r.name for r in group)
                continue
            searched_to_original = {name: original for original, name, _ in searches}
            for name, cands in parse_sparql_batch(data).items():
                original = searched_to_original.get(name, name)  # 去重音搜索名映射回原名
                candidates_by_name.setdefault(original, {}).update(cands)
        print(f"[pass {pass_idx + 1}] 完成", flush=True)

    # 4) 职业标签兜底（本体洞：Q4964182 之类不在闭包里的 philosopher 职业项）
    seen_occs: set[str] = set()
    for per in candidates_by_name.values():
        for cand in per.values():
            seen_occs.update(cand.occupations)
    seen_occs.update(q for qid in entity_by_qid for q in entity_by_qid[qid].occupations)
    want_occs = sorted(seen_occs - replay["phil_occs"])
    phil_occs = set(replay["phil_occs"])
    for start in range(0, len(want_occs), OCC_CHUNK):
        chunk = want_occs[start : start + OCC_CHUNK]
        data = _run_sparql(client, cache, "sparql:occ-labels:" + "|".join(chunk),
                           build_occ_labels_query(chunk), tag="occ-labels")
        if data is not None:
            phil_occs |= parse_occ_labels(data)

    # 5) 汇总 + 三信号职业过滤
    final = snapshot()
    for row in rows:
        kept, _ = apply_occupation_filter(final[row.atlas_id], closure, phil_occs)
        final[row.atlas_id] = kept
    print(f"[occ] 闭包 {len(closure) if closure else 0} 项 + 标签措辞 {len(phil_occs)} 项", flush=True)
    return final, skipped_names, closure is not None


def _pass_lang(pass_idx: int) -> str:
    return ("pt", "en", "en 去重音", "全文兜底")[pass_idx]


# ------------------------------------------------------------------ 主流程


def _load_atlas_csv(settings: Settings, cache: Cache, csv_arg: str | None, offline: bool) -> str:
    if csv_arg and not csv_arg.startswith("http"):
        return Path(csv_arg).read_text(encoding="utf-8")
    cached = cache.get("atlas:philosophers.csv")
    if cached is not None:
        return cached["text"]
    if offline:
        raise RuntimeError("离线模式但缓存里没有 philosophers.csv——先在线跑一次")
    import httpx

    url = csv_arg or f"{settings.atlas_base.rstrip('/')}/{ATLAS_CSV_PATH}"
    for wait in (2.0, 6.0):  # 静态文件两连试，不恋战
        try:
            with httpx.Client(headers={"User-Agent": UA}, timeout=30.0, follow_redirects=True) as client:
                resp = client.get(url)
                resp.raise_for_status()
                text = resp.text
            cache.put("atlas:philosophers.csv", {"text": text})
            return text
        except Exception as exc:  # noqa: BLE001
            print(f"    [atlas-csv] 拉取失败（{type(exc).__name__}）→ 等 {wait:.0f}s 重试", flush=True)
            time.sleep(wait)
    raise RuntimeError("Atlas philosophers.csv 拉取失败")


def run(args: argparse.Namespace) -> dict:
    settings = Settings.from_env()
    if args.db:
        settings.db_path = args.db
    if args.sparql_endpoint:
        settings.wikidata_sparql = args.sparql_endpoint

    cache = Cache(Path(args.cache_dir), refresh=args.refresh)
    client = None if args.offline else SparqlClient(settings.wikidata_sparql, timeout=args.timeout)

    rows = load_atlas_philosophers(_load_atlas_csv(settings, cache, args.csv, args.offline))
    if args.limit:
        rows = rows[: args.limit]
    print(f"[atlas] {len(rows)} 人待对齐", flush=True)

    replay = replay_cache(cache)
    print(f"[replay] 缓存回放：sparql 批候选 {len(replay['candidates_by_name'])} 名、"
          f"wbsearch {len(replay['search_qids_by_name'])} 名、实体 {len(replay['entity_by_qid'])} 个、"
          f"闭包 {'有' if replay['closure'] else '无'}、philosopher 职业项 {len(replay['phil_occs'])}", flush=True)

    candidates_by_atlas, skipped_names, has_closure = collect_candidates(
        rows, client, cache, replay, args.offline
    )
    transport = "sparqlwrapper" if not args.offline else "cache-only"

    artifact = build_alignment(
        rows,
        candidates_by_atlas,
        transport=transport,
        occupation_filter="closure" if has_closure else "loose",
        generated_at=datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        skipped_names=skipped_names,
    )
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2), encoding="utf-8")
    stats = artifact["stats"]
    print(
        f"[align] {stats['aligned']}/{stats['total']} 对齐"
        f"（{stats['rate']:.1%}） confidence={stats['confidence']}"
        f" name_en={stats['name_en_filled']} name_zh={stats['name_zh_filled']}"
        f" transport={transport}",
        flush=True,
    )
    for miss in artifact["unmatched"][:15]:
        print(f"    未对齐 atlas_id={miss['atlas_id']} {miss['name_pt']!r}: {miss['reason']}", flush=True)
    if len(artifact["unmatched"]) > 15:
        print(f"    …共 {len(artifact['unmatched'])} 条未对齐，见工件", flush=True)

    if args.apply:
        apply_to_db(settings, artifact, out_path)
    return stats


def apply_to_db(settings: Settings, artifact: dict, artifact_path: Path) -> None:
    """把对齐结果 UPDATE 进 philosophers（只动已存在的行），ingest_log 记 source='wikidata'。"""
    aligned = artifact["aligned"]
    stats = artifact["stats"]
    conn = settings.connect()
    try:
        schema.init(conn)  # CREATE IF NOT EXISTS，不改表结构；保证 ingest_log 存在
        existing = conn.execute("SELECT COUNT(*) FROM philosophers").fetchone()[0]
        if existing == 0:
            print("[apply] philosophers 表为空（task 2.2 尚未摄取）——跳过 apply；"
                  "JSON 工件是权威产物，入库后可重跑 --apply 回填", flush=True)
            return
        now = datetime.now(timezone.utc).isoformat(timespec="microseconds")
        with conn:
            updated = 0
            for row in aligned:
                cur = conn.execute(
                    "UPDATE philosophers SET wikidata_qid = ?,"
                    " name_en = COALESCE(?, name_en), name_zh = COALESCE(?, name_zh)"
                    " WHERE atlas_id = ?",
                    (row["qid"], row["name_en"], row["name_zh"], row["atlas_id"]),
                )
                updated += cur.rowcount
            status = "ok" if stats["rate"] >= 0.9 else "partial"
            conn.execute(
                "INSERT INTO ingest_log (source, status, detail, as_of) VALUES (?,?,?,?)",
                (
                    SOURCE,
                    status,
                    json.dumps(
                        {
                            "aligned": stats["aligned"],
                            "total": stats["total"],
                            "rate": stats["rate"],
                            "confidence": stats["confidence"],
                            "updated_rows": updated,
                            "artifact": str(artifact_path),
                            "transport": artifact["meta"]["transport"],
                        },
                        ensure_ascii=False,
                    ),
                    now,
                ),
            )
        print(f"[apply] UPDATE {updated}/{existing} 行（philosophers），ingest_log 记 source='wikidata'"
              f" status={status}", flush=True)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wikidata 摄取器：Atlas 人物对齐 QID + 回填（task 2.3）")
    parser.add_argument("--apply", action="store_true", help="把对齐结果 UPDATE 进 philosophers 表")
    parser.add_argument("--db", default=None, help="SQLite 路径（默认 FD_ZHESI_REF_DB 或 ~/.fd-zhesi/reference.db）")
    parser.add_argument("--csv", default=None, help="Atlas philosophers.csv 的 URL 或本地路径（默认站点原始 URL，带缓存）")
    parser.add_argument("--out", default=str(ROOT / "scripts/artifacts/wikidata_alignment.json"), help="对齐工件输出路径")
    parser.add_argument("--cache-dir", default=str(ROOT / "scripts/artifacts/wikidata_cache"), help="请求缓存目录（断点续跑）")
    parser.add_argument("--limit", type=int, default=0, help="只处理前 N 人（冒烟测试）")
    parser.add_argument("--timeout", type=int, default=45, help="SPARQL 查询超时（秒）")
    parser.add_argument("--refresh", action="store_true", help="忽略缓存强制重取")
    parser.add_argument("--offline", action="store_true", help="完全离线：只用缓存重算工件")
    parser.add_argument("--sparql-endpoint", default=None, help="覆盖 SPARQL 端点（默认 Settings.wikidata_sparql）")
    args = parser.parse_args(argv)

    stats = run(args)
    return 0 if stats["aligned"] > 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
