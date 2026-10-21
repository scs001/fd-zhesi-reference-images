# 哲思参照系数据许可台账

> change: `zhesi-worldview-workshop` task 1.1。结论必须给出明确判定与出处；本表是摄取前置门的事实记录。

## 结论一览（2026-10-20 核验）

| 源 | 用途 | 许可 | 判定 | 署名/约束 |
|---|---|---|---|---|
| Philosophy Atlas | 种子图谱（376 人×六轴、804 关系边含极性、topics） | **CC BY 4.0**（whitepaper v2.1 明示 "Dados abertos sob licença CC BY 4.0"） | **允许（需署名）** | 结果 attribution 带 "Philosophy Atlas (MBGuedes), CC BY 4.0" 与源链接 |
| Stanford Encyclopedia of Philosophy | 概念引用层 | 站点条款限制批量复制 | **允许（只链不抄）** | 仅存标题+摘要+URL，不复制正文 |
| Wikidata | 全量层（人物/关系/时间线） | CC0 | **允许（无约束）** | 惯例仍带来源标注 |
| PhilPapers Survey | 立场分布 | 站点内容，Cloudflare 后 | **允许（低频快照引用，注明轮次年份）** | 2009/2020 轮数据带年份标注；不做全文镜像 |

## 核验出处

- Philosophy Atlas whitepaper v2.1（2026-06）：`https://philosophyatlas.com.br/web/whitepaper.html`
  - 关键句（页脚）："Dados abertos sob licença CC BY 4.0. Desenvolvido por MBGuedes"
  - 语料口径：376 filósofos / 804 conexões / 117 escolas；边类型三种（positiva/crítica/sintética）
  - 数据文件直取路径：`data/raw/philosophers.csv`、`data/raw/influences.csv`、`data/catalogue/topics/topics.json`
- SEP 许可立场：设计决策 D2（引用层只链不抄），源自信约通行的 no-bulk-copy 条款
- Wikidata：CC0（https://www.wikidata.org/wiki/Wikidata:Licensing）

## 影响与后续

- design.md D2 的「许可不允许可降级 Wikidata-only」分支**不触发**——Atlas 许可通过，按主路径摄取
- Atlas 白皮书还提到两份 working paper 已上 Zenodo 带 DOI（§9），如需引用数据集版本，摄取时在 provenance 里带 whitepaper 版本号（v2.1 / corpus v8.0）
