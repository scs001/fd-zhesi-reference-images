# fd-zhesi-reference

哲思（zhesi 线）参照系 MCP：四层外部真源的对接组装与查询——SEP 引用层（概念）、
Philosophy Atlas 种子图谱（376 人×六轴 + 极性关系边）、Wikidata 全量层（SPARQL/CC0）、
PhilPapers Survey 立法分布。**对接组装，不自建抽取**（academic-graph-connector 先例）。

新仓依据 `openspec/changes/zhesi-worldview-workshop`（design D1：照 fd-legal-search-mcp 分仓先例）。

## 工具面（契约）

| 工具 | 能力 | 数据源 | 实现跟踪 |
|---|---|---|---|
| `concept_lookup` | 哲学词语定义摘要 + SEP/维基引用链接（正文不落库） | SEP `search/searcher.py` | task 2.4 |
| `philosopher_query` | 人物：生卒/学派/时代/六轴坐标 | Atlas `philosophers.csv` + Wikidata 对齐 | task 2.2 |
| `relation_query` | 关系边：极性（positive/critical/synthetic）+ 权重 | Atlas `influences.csv` | task 2.2 |
| `stance_distribution` | 经典立场问题的职业哲学家立场分布（带轮次年份） | PhilPapers Survey（2009/2020） | task 2.5 |
| `source_status` | 各源摄取状态与缺口（未摄取必带原因） | 摄取台账表 | task 2.6 |

骨架阶段：全部工具以诚实错误回报未接线状态（消息含 tracking task），不返回编造数据；
未命中一律如实返回空结果并带原因（blocked/upstream/unreachable/not_indexed）。

## 溯源契约

每个结果携带 `provenance`：`source` / `stable_id` / `as_of` / `attribution`。
Atlas 数据为 **CC BY 4.0**，attribution 必须随结果下发（见 `docs/data-license.md`）。
缺失即 bug，不做降级。

## 存储

参照系库是可重建缓存（design D3），v1 用 SQLite 单文件（`FD_ZHESI_REF_DB`，默认
`~/.fd-zhesi/reference.db`），与个人层（fd-zhesi 产品库）物理分库。schema 中西无感
（design D7）：school/region 为开放词表，不绑西哲枚举。

## 本地开发

```bash
pip install -e ".[dev]"
pytest
fd-zhesi-reference serve --port 8000   # /mcp + /healthz
```
