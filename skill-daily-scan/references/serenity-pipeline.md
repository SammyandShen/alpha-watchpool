# serenity 9 段式 → hypothesis JSON 字段映射

方法论本体见全局 skill `~/.claude/skills/serenity-alpha/SKILL.md`（同机可读，分析时先读它）。本文件只定义**分析结果如何 JSON 化落盘**，不重复方法论正文。

## 字段映射表

| serenity 输出段 | hypothesis JSON 字段 | 说明 |
|---|---|---|
| 结论先行（primary candidate） | `ticker` / `company` / `thesis.one_liner` | one_liner ≤ 40 字，格式「<需求变化>将在 N 个季度内改写其<收入线>」 |
| A. 表层新闻 | `source_news[]` | `{date, headline, url, summary}`，summary ≤ 60 字 |
| B. 已发生的需求变化 | `thesis.demand_change` | 只写已观察到的事实，不写推测 |
| C. 财务翻译 | `thesis.transmission` | 点名具体收入线/毛利项/现金流项 |
| D. 受益链条 | （不单独存）| 一阶受益者即 primary candidate；二三阶写进 `links.related` 的备选假设或放弃 |
| E. 小市值高弹性标的 | `ticker` 选择依据 + `thesis.serenity_score.elasticity/purity` | |
| F. 市场误分类 | `thesis.misclassification` | 「市场当它是 X，它正在变成 Y」句式 |
| 全链路叙事 | `thesis.brief_cn`（字符串数组，3-4 段）| 看板「判断链路」区展示。段落顺序：①触发新闻是什么 ②需求为何已可观察+财务传导 ③市场误分类 ④先验为什么给这个数（扣分项）+赌的是什么 |
| G. 验证指标 | `validation_conditions[]` | 见下方规则 |
| H. 下行风险 | `thesis.serenity_score.downside` + 周报叙事 | 分数 5=下行小 |
| I. 仓位建议 | 不落盘 | 仓位是 promoted 之后的事，池内只跟踪判断质量 |
| 七维评分表 | `thesis.serenity_score` | 7 个 1-5 整数 |

## 新假设 JSON 完整模板

```json
{
  "id": "H<YYYYMMDD>-<TICKER>",
  "ticker": "ONTO",
  "company": "Onto Innovation",
  "created_at": "<今天>",
  "status": "observing",
  "status_history": [
    { "at": "<今天>", "from": null, "to": "observing", "reason": "入池：<一句话新闻来源>" }
  ],
  "source_news": [
    { "date": "<新闻日期>", "headline": "...", "url": "...", "summary": "..." }
  ],
  "thesis": {
    "one_liner": "...",
    "demand_change": "...",
    "transmission": "...",
    "misclassification": "...",
    "brief_cn": ["①触发新闻...", "②需求与传导...", "③市场误分类...", "④先验理由与赌点..."],
    "serenity_score": {
      "demand_certainty": 4, "transmission_clarity": 3, "purity": 4,
      "elasticity": 4, "neglect": 3, "verification_speed": 4, "downside": 3
    }
  },
  "prior": {
    "p": 0.30,
    "log_odds": -0.8473,
    "rationale": "七维总分 25 → 0.22-0.32 档；<一句话调整理由>",
    "set_by": "daily-scan <今天>"
  },
  "current": {
    "p": 0.30,
    "log_odds": -0.8473,
    "last_reviewed": "<今天>",
    "recomputed_from_ledger": true
  },
  "validation_conditions": [
    {
      "id": "V1", "required": true,
      "metric": "<具体财报指标>",
      "confirm_if": ">= <数字>", "falsify_if": "< <数字>",
      "check_by": "<YYYY-MM-DD，通常下次财报日>", "status": "pending"
    }
  ],
  "horizon": { "quarters": 2, "review_deadline": "<created_at + quarters*3 个月>" },
  "baseline": {
    "entry_date": "<今天>",
    "entry_price": 0.0,
    "benchmark": "SPY",
    "benchmark_entry_price": 0.0,
    "sector_benchmark": "<行业 ETF>",
    "sector_benchmark_entry_price": 0.0
  },
  "evidence_log": [],
  "posterior_history": [
    { "date": "<今天>", "p": 0.30, "note": "prior" }
  ],
  "events": [],
  "links": { "supersedes": null, "related": [] },
  "resolution": {
    "outcome": null, "resolved_at": null,
    "brier_final": null, "brier_time_avg": null,
    "price_return_pct": null, "excess_vs_spy_pct": null, "excess_vs_sector_pct": null,
    "post_mortem": null
  }
}
```

## 先验映射表（七维总分 → prior.p 档位）

| 七维总分（7-35） | prior.p 区间 | 直觉 |
|---|---|---|
| ≤ 20 | 0.15 – 0.22 | 弱假设，纯观察 |
| 21 – 25 | 0.22 – 0.32 | 标准新闻驱动假设 |
| 26 – 30 | 0.30 – 0.42 | 强假设：需求确定 + 传导清晰 |
| > 30 | 0.38 – 0.45 | 罕见；接近硬上限 |

- **硬上限 0.45**——入池时没有任何 T1 证据的假设不配 50%+
- 在档位区间内取值的微调理由写进 `prior.rationale`
- `prior.log_odds = ln(p/(1-p))`，保留 4 位小数（可用 python 一行算，禁止心算）

## 常用 log_odds 速查

| p | log_odds |
|---|---|
| 0.15 | -1.7346 |
| 0.20 | -1.3863 |
| 0.25 | -1.0986 |
| 0.30 | -0.8473 |
| 0.35 | -0.6190 |
| 0.40 | -0.4055 |
| 0.45 | -0.2007 |

## 验证条件（validation_conditions）设计规则

1. **至少 1 条 required=true**，绑定未来 1-4 个季度内的具体财报节点
2. confirm_if / falsify_if 必须**可客观判定**：数字阈值（收入 YoY、毛利率、backlog）或明确事实（管理层主动量化提及）。禁止「表现良好」这类模糊表述
3. confirm 与 falsify 之间留灰区是正常的（灰区 = 继续观察）
4. `check_by` 通常 = 下次/下下次财报日；不知道确切日期就取该季度末 + 40 天
5. 加分项：再配 1-2 条 required=false 的旁证条件（供应链、客户、竞对）

## Ticker 格式（美股 + A 股）

- 美股：直接用交易所代码，如 `OUST`
- A 股用 Yahoo 后缀格式：上交所 `600519.SS`、深交所 `000858.SZ`、北交所 `835185.BJ`
- id 格式不变：`H20260705-600519.SS` 合法

## 基准选择

**美股**：`benchmark` 固定 `SPY`；`sector_benchmark` 按行业选：半导体 `SOXX`、软件 `IGV`、生科 `XBI`、能源 `XLE`、工业 `XLI`、金融 `XLF`、非必选消费 `XLY`、通信 `XLC`、公用 `XLU`、医疗 `XLV`、材料 `XLB`、必选消费 `XLP`、房地产 `XLRE`、航天防务 `ITA`、网络安全 `HACK`、云 `SKYY`

**A 股**：`benchmark` 固定 `510300.SS`（沪深300ETF）；`sector_benchmark` 按行业选场内 ETF：半导体 `512480.SS`、新能源车 `515030.SS`、光伏 `515790.SS`、医药 `512010.SS`、军工 `512660.SS`、酒/消费 `512690.SS`、证券 `512000.SS`、银行 `512800.SS`、计算机/AI `512720.SS`、机器人 `562500.SS`、有色 `512400.SS`、化工 `516020.SS`；小盘弹性主题可用中证1000ETF `512100.SS` 兜底

- 入池当天的 entry_price / benchmark_entry_price / sector_benchmark_entry_price：优先取 `docs/data/prices.json` 里当天数据；没有则用最近收盘价并在次日由 sync-prices.py 回填校正
- 收益对比同币种进行（A 股 vs A 股基准、美股 vs 美股基准），超额收益天然币种中性

## A 股财报节奏（check_by 设定用）

| 报告 | 法定截止 | 业绩预告窗口 |
|---|---|---|
| 一季报 | 4-30 | 无强制，部分 4 月上旬 |
| 中报 | 8-31 | 7-15 前（预增/预减/扭亏/首亏 强制）|
| 三季报 | 10-31 | 无强制 |
| 年报 | 次年 4-30 | 1-31 前（强制情形同上）|

- **业绩预告/快报是 A 股独有的 T1 提前量**：验证条件的 check_by 可以直接绑预告窗口而非正式财报日，验证速度 +1 档
- 中标公告、涨价函、扩产公告（巨潮资讯网披露）均属 T1 级公司公告
