---
name: alpha-daily-scan
description: 每日扫描市场新闻，用 serenity-alpha 方法论把「已发生的需求变化」转化为可检验的 alpha 假设并加入观察池（data/hypotheses.json），同时对池内活跃假设做轻跟踪（pending 证据记账、重大事件标记）。当用户说「跑今天的 alpha 扫描」「每日扫描」「daily scan」或由 launchd 定时触发时使用。不做贝叶斯更新（那是 alpha-weekly-review 的职责）。
---

# Alpha Daily Scan（每日扫描 + 轻跟踪）

## 职责边界

**做**：扫描新闻 → 产出新假设入池；给活跃假设追加 pending 证据；标记重大事件。
**不做**：贝叶斯更新后验（唯一例外见「T1 紧急通道」）；新建同 ticker 重复假设；修改 scorecard/prices/dashboard（脚本管）。

所有产出是研究假设，不是投资建议。

## 流程

### 0. 读状态

- `data/hypotheses.json` — 活跃假设清单（status=observing）及其 ticker
- `data/scan-log.json` — 近 14 天新闻指纹（去重）
- 顺带清理 scan-log 中超过 14 天的条目

### 1. 新闻扫描（信源优先级）

0. **A 股结构化信号（第一优先）**：读 `data/cn-forecasts.json`（shell 前置已由 fetch-cn-forecasts.py 生成）：
   - `positive_forecasts`：近 7 天利好业绩预告（预增/略增/扭亏/续盈），按扣非净利增幅降序——这是 T1 级候选 feed，从头部挑「增幅大 + 有可持续需求逻辑」的做 serenity 分析（一次性损益/资产处置类的跳过）
   - `pool_negative_warnings`：池内活跃 ticker 的利空预告——**必查**，命中即走 T1 紧急通道
1. **探测 FMP MCP**：若会话中存在 FMP 金融数据工具（news / search 等，工具名形如 `mcp__*__news`），优先使用：拉当日 general news + 活跃 ticker 相关新闻。press-releases 先按关键词过滤（订单/backlog/扩产/涨价/中标/supply agreement/guidance），律所诉讼通稿直接丢弃。
2. **WebSearch 兜底**（headless 必走）：**美股 + A 股各 2-4 组**定向查询，围绕「已发生的需求变化」信号：
   - 美股：当日/昨日财报超预期 + 指引上修（尤其中小盘）；供应链信号（交期拉长、涨价、产能吃紧、backlog、book-to-bill）；大客户 capex/采购/订单公告；技术产品出货放量、渗透率拐点
   - A 股（中文查询）：业绩预告 预增/超预期；中标公告 大订单；涨价函 提价 产能满载；扩产公告 供不应求。优先信源：财联社、巨潮资讯（公司公告）、证券时报、上证报
   - 池内活跃 ticker 的最新动态（每 ticker 一次快查；A 股查中文名+公告）
   - 覆盖范围：美股 + 中国 A 股（沪/深/北交所，Yahoo 后缀格式见 serenity-pipeline.md）。FMP MCP 对 A 股覆盖弱，A 股信号主要靠 WebSearch
3. 对每条候选新闻计算指纹：`YYYY-MM-DD + 核心实体 + 事件类型` 的短哈希描述；命中 scan-log 已有指纹的跳过。

### 2. 需求过滤（serenity 第 1 步）

只有通过「需求已可观察」过滤的新闻才进入完整分析：用户在付费/采购在扩大/供应商在出货或涨价/排产在收紧/财报电话会已提及。纯叙事（愿景、传闻、分析师观点）→ 记入 scan-log（action: `skipped_watchlist_only`），不建假设。

### 3. serenity 分析 → 假设入池

对通过过滤的新闻执行 serenity-alpha 完整 9 段式分析（方法论本体见 `~/.claude/skills/serenity-alpha/SKILL.md`），然后按 `references/serenity-pipeline.md` 的字段映射把分析结果 JSON 化，写入 `data/hypotheses.json`。

**新 ticker 首次入池时，同步写公司中文档案** `data/company-notes.json`（key = ticker）：
- `business_cn`：主营业务一段话（产品线、应用场景、商业模式）
- `segments_cn`：收入结构/业务构成一句话
- `position_cn`：行业地位与竞争格局一句话
- `added_at`：今天
数字性指标（市值/营收/毛利率等）**不要写**——`sync-profiles.py` 自动拉取。已有档案的 ticker 不重复写。

**入池纪律**：
- 每日新建假设上限 **2 条**——多于 2 个候选时按 serenity 七维总分排序取最高的
- 先验由七维总分查映射表（见 serenity-pipeline.md），**硬上限 0.45**
- `baseline.entry_price` 用最近收盘价（FMP quote 或 WebSearch 查询；若盘中，用前收盘），基准固定 SPY + 一个行业 ETF（半导体 SOXX、软件 IGV、生物科技 XBI 等，按标的行业选）
- 验证条件必须至少 1 条 required=true，且 confirm_if / falsify_if 都是可客观判定的数字或事实

### 4. 去重规则（写死）

| 情形 | 动作 |
|---|---|
| 同 ticker 已有 observing 假设 | **不新建**。新闻作为 pending 证据追加到既有假设 evidence_log（likelihood 填 `{"label": null, "lr": null, "log_lr": null}`，`"pending": true`）|
| 同 ticker 假设已 falsified / expired | 允许新建，`links.supersedes` 指向旧 id |
| 同一新闻指向多个 ticker | 允许各建一条（受每日 2 条上限约束），`links.related` 互指 |

### 5. 池内轻跟踪

对每个活跃 ticker：
- **财报日校正**：读 `docs/data/profiles.json` 各 ticker 的 `next_earnings_date`（Yahoo 官方排期，每日刷新），与 `events[]` 中 earnings 事件比对，不一致则更新并去掉「预估待确认」字样；A 股该字段常为空，按法定节奏（serenity-pipeline.md）估算
- 快查重大事件（财报日临近 7 天内、并购、指引修正、大订单）→ 追加/更新 `events[]`
- 新的相关新闻 → pending 证据入账（不评 LR，周复核统一评）

### 6. T1 紧急通道（唯一允许每日动后验的情形）

若发现 **T1 级决定性事件**（本公司财报/8-K 直接击穿某 required 验证条件的 confirm_if 或 falsify_if）：
1. 追加证据，按九档标尺定标签（此时允许 `"pending": false`）
2. 用 `python3 scripts/compute-metrics.py` 重算后验（自己不手算）
3. 在最终报告中显著标注，git commit message 会带 `[URGENT]`（shell 层处理，你只需在输出第一行写 `URGENT: <ticker> <事件>`）

### 7. 落盘 + 自检

1. 更新 `data/hypotheses.json`（`$last_updated` 改为今天）和 `data/scan-log.json`
2. 运行 `python3 scripts/validate-schema.py`——**必须通过**；报错则修复后重跑，直到通过为止
3. 输出简短 markdown 总结：扫描了几条新闻、新建几条假设（ticker + 一句话论点 + 先验）、追加了几条 pending 证据、标记了什么事件、跳过原因统计

## 禁止事项

- 禁止手算/修改 current.p、current.log_odds（T1 紧急通道也要走 compute-metrics.py）
- 禁止改 posterior_history（那是周复核的账）
- 禁止编辑 docs/ 下任何文件
- 价格快照不归本 skill 管（shell 前置已跑 sync-prices.py）
