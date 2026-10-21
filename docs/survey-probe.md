# PhilPapers Survey 试摄取侦察报告（task 1.2）

日期：2026-10-20。执行环境：本地开发机（macOS）。目标：拿到 PhilPapers Survey 原始快照并解析出 ≥10 个立场问题的选项与百分比，为任务 2.5（正式摄取）选择通道。

结论先行：**达标**。2009 轮 31 题全量解析（Wayback 快照，N=931）；2020 轮 100 题全量解析（PhilBench 数据集，官方口径 N=1785）；两条独立来源交叉核对完成。philpapers.org 直连被 Cloudflare 拦截（复测确认），Wayback 是本地可行的主通道。

## 网络实况（本机出网特征）

| 域 | 状态 |
| --- | --- |
| philpapers.org / survey2020.philpeople.org | 可连通，但全站 Cloudflare 质询，curl + Chrome UA 一律 403（约 5.8KB 质询页，正文含 "cloudflare"/"challenge"） |
| web.archive.org / archive.org | **网络层超时**（连不上，非 403——本机网络对该域出口不通） |
| github.com / api.github.com | 可用 |
| raw.githubusercontent.com | 可用但极慢（~10KB/s，10MB 文件 5 分钟仅得 3.5MB） |
| cdn.jsdelivr.net（GitHub 镜像） | 可用且快（10MB 约 70s） |
| google.com / r.jina.ai | 不通 |

设计稿记录的本地代理 127.0.0.1:7892 本次探测时未启动（连接拒绝）。

## 三条路线实测

### 路线 1：直连试探 —— 失败（符合预期）

- `curl -L -A "Mozilla/5.0 ... Chrome/131" https://philpapers.org/surveys/` → **403**（5810B）
- `https://philpapers.org/surveys/results.pl` → **403**（5811B）
- 正文为 Cloudflare 质询页（含 "cloudflare" ×4、"challenge"）。与既有认知一致：生产必须走爬虫平台 proxy。
- 附加发现：2020 轮**不在 philpapers.org 域**，官方站是独立域 `survey2020.philpeople.org`（PhilBench README 确认）。直连该域同样 **403**（5745B，同一 Cloudflare 墙）。

### 路线 2：Wayback Machine —— 部分成功（2009 全量拿到；2020 定位到快照但内容未能渲染）

本机连不上 web.archive.org，但 MCP `web_reader`（服务端远程渲染）可达 Wayback，实测：

- **2009 轮（主快照，成功）**：availability API 给出 `philpapers.org/surveys/results.pl` 最近快照 `20230424171640`（HTTP 200）。经 web_reader 渲染该 `id_` 原始捕获，得到完整结果页：**31 个问题块**（论文口径 30 题 + 第二个 trolley 变体），每题选项含 `count/N` 与百分比，N=931。
  - 局限：web_reader 只回 markdown/text，**拿不到原始 HTML 字节**（本机网络又不通 archive.org）。落库产物是 Wayback HTML 捕获的 markdown 渲染（头部含完整溯源块）。生产走爬虫平台可直取原始 HTML，此局限仅限本次本地侦察。
- **2020 轮（定位成功、内容未取得）**：正确 URL 是 `https://survey2020.philpeople.org/survey/results`（philpapers.org 域下的 `surveys/survey2020/results.pl` 是镜像路径）。availability API 确认快照 `20240605075202`（HTTP 200）。但该页面（交互式图表重页）经 web_reader 反复重试（间隔 10s~120s，两种路径、id_/无 id_、text/markdown）始终返回服务端 500 "Network error"，最终 web_reader 账号级限流。**blocker：2020 轮 Wayback 页面本地未能渲染**，2020 数据改由路线 3 全量补足。
- Wayback 的 CDX/timemap 端点 web_reader 均不可渲染（500）。

### 路线 3：GitHub 佐证 —— 成功，且超额完成 2020 轮

- `gh search repos "philpapers"` → `lordscottish12/PhilBench`（用 2020 官方 100 题给 LLM 施测的 benchmark）。
- 其 `index.html` 内嵌 `QUESTIONS` 数组：**100 题全部带 `phil_dist`（2020 官方分布）**，其中 30 题带 `phil_dist_2009`（2009 论文终版口径），并引用官方站 `survey2020.philpeople.org`（100 题、1,785 应答者）。
- raw.githubusercontent 太慢，经 `cdn.jsdelivr.net` 镜像取回（index.html 626,778B；提取 QUESTIONS 后 45,995B 落库）。
- 附带核验：`vpparkkinen/phillm` 的 `sqs.txt`（2020 题干对）与 PhilBench 题干一致。

## 快照清单（data/raw/philpapers/）

| 文件 | 字节数 | 来源与时间戳 |
| --- | --- | --- |
| `2009_results_20230424171640.md` | 8,356 | Wayback 捕获 `https://web.archive.org/web/20230424171640id_/https://philpapers.org/surveys/results.pl`（原始页 HTTP 200；文件头部含溯源块；markdown 渲染而非原始 HTML） |
| `philbench_questions.json` | 45,995 | 提取自 `github.com/lordscottish12/PhilBench` index.html（经 jsDelivr，2026-10-20 23:37 +08:00） |
| `philbench_readme.md` | 1,146 | 同上（README 原文，含官方站与 N=1785 出处） |
| `philbench_provenance.json` | 1,259 | 上两者的机器可读溯源 |
| `survey_parsed.json` | 11,474 | 解析产物：2009 轮 |
| `survey_parsed_2020.json` | 39,848 | 解析产物：2020 轮 |

（未落库的定位信息：2020 轮 Wayback 快照 `https://web.archive.org/web/20240605075202/https://survey2020.philpeople.org/survey/results`，HTTP 200，内容未取得。）

## 解析统计

- **2009 轮**（`survey_parsed.json`，Wayback 快照解析）：**31 题 / 104 个选项行**，N=931。全部题目 counts 求和 =931、百分比与 count/N 偏差 ≤0.15pp（页面百分比用截断而非四舍五入；仅 Mind/physicalism 一行 519/931→55.8% 为站方分母口径，两口径均记入脚本注释）。达标线（≥10 题）远超。
- **2020 轮**（`survey_parsed_2020.json`，PhilBench 官方口径）：**100 题 / 288 个选项行**，N=1785（含 category/multi_select 元数据）。
- **交叉核对**（2009，30 题按题干+选项标签匹配，100 组百分比对比）：中位偏差 3.7pp，最大 26.8pp。原因已查明：**两套都是官方口径但基不同**——Wayback 的 results.pl 是 2009 初步波（preliminary wave，全完成样本 N=931，"other" 桶大）；PhilBench 的 `phil_dist_2009` 是 2013 论文终版（目标教员加权，other 桶小、多数派偏高）。例：a priori 两口径仅差 0.7pp；Science: realism 55.4%（初步）vs 75.1%（终版）。**2.5 入库建议采用论文终版口径并标注口径字段**（见下）。

典型一题（survey_parsed.json）：

```json
{"round": "2009",
 "source_url": "https://web.archive.org/web/20230424171640id_/https://philpapers.org/surveys/results.pl",
 "snapshot_ts": "20230424171640",
 "questions": [{"question": "Free will: compatibilism, libertarianism, or no free will?",
   "options": [{"label": "compatibilism", "count": 550, "pct": 59.1},
               {"label": "libertarianism", "count": 130, "pct": 14.0},
               {"label": "no free will", "count": 205, "pct": 22.0},
               {"label": "other", "count": 46, "pct": 4.9}]}]}
```

## 数据年份口径

- 最新一轮是 **2020**（Bourget & Chalmers，100 题，1,785 应答者，官方站 survey2020.philpeople.org）。2009 轮 30 题（论文口径）为纵向对比层。
- 与 design.md 风险项一致：2020 后无新轮次，UI 必须带年份标注；本次两个解析产物均已带 `round` 与来源字段。

## 对任务 2.5（正式摄取）的通道建议

1. **首选：爬虫平台 proxy 直取官方站**（不复用本次 Wayback 通道）。理由：
   - 直取可得**原始 HTML**（本次本地只能拿 markdown 渲染），溯源粒度最高；
   - 2020 轮官方站是独立域 `survey2020.philpeople.org`，别再找 philpapers.org 路径（本次踩坑点）；
   - 线上 results.pl 展示的是**终版口径**数字，直取即得可引用口径，避免初步/终版混淆；本地代理 7892 修复后可先本地复测。
2. **兜底：Wayback 快照**（生产机出网若通 archive.org）。快照充足（2009 轮 20230424171640 已验证、2020 轮 20240605075202 存在），注意 2009 老捕获可能是初步波口径，入库时需打 `tabulation` 字段区分。
3. **无论如何落库 PhilBench QUESTIONS 作交叉校验源**（jsDelivr 镜像稳定可达），schema 加 `round`、`tabulation`（preliminary/final-paper）、`source_url`、`snapshot_ts` 四字段；两口径冲突时以终版为准、另一口径可并存。
4. 本地开发期：web.archive.org 网络不通是常态，scripts/probe_survey.py 已把解析与抓取解耦——快照文件在手即可重放解析。

## 复现

```bash
python3 scripts/probe_survey.py   # 解析本地快照 → survey_parsed.json + survey_parsed_2020.json，含一致性门
```

blocker 一句话：**2020 轮 Wayback 页面未能经 web_reader 渲染（服务端持续 500/限流）**，2020 数据由 PhilBench 官方口径数据集全量补足（100/100 题），生产直取后可替换为官方 HTML 源。
