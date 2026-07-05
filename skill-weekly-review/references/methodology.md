# 贝叶斯更新方法论（v1）

> 本文件是整个系统的灵魂。目标：让 LLM 的信念更新**可复现、抗锚定、不过度自信**。
> 三条设计原则：①语言标尺离散化（不让 LLM 自由发明数字）②对数几率账本化（后验永远从账本重算）③结构性封顶（用规则而非自觉压制过度自信）。
> 修改本文件需用户确认，并把 hypotheses.json 的 `$methodology_version` +1。

## 1. 命题的精确定义

每条假设被评估的命题统一为：

> **「该假设全部 required=true 的验证条件，在 horizon 内达成」**

不是「股价会涨」。财务验证与赚钱分开记账（scorecard 的轨道 A / 轨道 B）。这保证 ground truth 客观可判，Brier 分数才有意义。

## 2. 证据分级（tier 决定 LR 上限）

| tier | 定义 | 例子 | 允许 LR 范围 |
|---|---|---|---|
| `T1_hard_financial` | 本公司财报 / 指引 / 8-K / 交易所公告中的数字（A 股：业绩预告/快报、中标/扩产/涨价公告） | 收入线 YoY、backlog、指引上修、业绩预增 | 0.2 – 5.0 |
| `T2_supply_chain` | 供应链上下游披露、客户/竞对财报旁证、行业出货数据、价格指数 | 客户 capex 上修、交期拉长 | 0.4 – 2.5 |
| `T3_soft_signal` | 管理层口头表态、渠道调研、分析师上调 | 电话会主动提及需求 | 0.67 – 1.5 |
| `T4_narrative` | 媒体报道、社交媒体、传闻 | 「据称」「知情人士」 | 固定 1.0（只记录，不更新）|
| `P_price` | 相对基准的超额收益（见 §4） | — | 0.67 – 1.5，且生命周期总量封顶 |

分级从严：拿不准 T1 还是 T2，按 T2；拿不准 T2 还是 T3，按 T3。

## 3. 似然比语言标尺（九选一，禁止自由发明数字）

评估口径强制使用鉴别力问句：**「如果假设为假，这条证据出现的概率有多大？」**
利好新闻在假设为假时也经常出现（行业 beta、普涨叙事）——这类证据必须降级到 weak / neutral。

| label | 含义（假设为真 vs 为假时看到该证据的概率之比） | LR | Δlog-odds |
|---|---|---|---|
| `decisive_support` | 几乎只有假设为真才会出现 | 5.0 | +1.609 |
| `strong_support` | 强支持 | 3.0 | +1.099 |
| `moderate_support` | 中等支持 | 2.0 | +0.693 |
| `weak_support` | 弱支持 | 1.5 | +0.405 |
| `neutral` | 无鉴别力 | 1.0 | 0 |
| `weak_against` | 弱反对 | 0.667 | −0.405 |
| `moderate_against` | 中等反对 | 0.5 | −0.693 |
| `strong_against` | 强反对 | 0.333 | −1.099 |
| `decisive_against` | 几乎只有假设为假才会出现 | 0.2 | −1.609 |

标签选定后 lr / log_lr 查表填写，validate-schema.py 会核对标签与数值一致、且不超 tier 上限。

## 4. 价格证据的特殊处理

- **只用相对收益**：`excess = ticker 入池以来涨幅 − sector_benchmark 同期涨幅`（缺行业基准用 SPY）。**绝对涨跌禁止作为证据**。
- 映射（由 compute-metrics.py 在 review-input.json 里给出建议标签，LLM 只能采纳或降级，不能升级）：

| excess | 建议标签 |
|---|---|
| ≥ +30% | moderate_support |
| +15% ~ +30% | weak_support |
| −15% ~ +15% | neutral |
| −30% ~ −15% | weak_against |
| ≤ −30% | moderate_against |

- **每周至多一条**价格证据；neutral 的价格证据不必入账。
- **全生命周期价格类 |Σ log_lr| 封顶 1.10**（≈ 一条强证据）。理由：价格反映市场共识变化，而假设的本质是「市场错了」——让市场投票权过大，系统退化成动量跟随。

## 5. 先验纪律

- 先验由 serenity 七维总分查映射表（见 skill-daily-scan/references/serenity-pipeline.md），**硬上限 0.45**。
- 新闻驱动假设的基础命中率就是低；入池时没有任何 T1 证据的假设不配 50%+。

## 6. 抗锚定与抗过度自信护栏

1. **相关性去重**：同一底层事件的多篇报道 = 一条证据。后续报道用 `correlates_with` 指向首条证据 id，LR 强制 1.0。每周复核先做证据聚类再评 LR。
2. **周更新总量封顶**：单次周复核对一条假设的 |Σ Δlog-odds| ≤ 2.2（≈两条强证据），除非本周含 T1 decisive 事件。超出时保留绝对值最大的证据，其余降为 neutral 并注明「capped」。
3. **后验夹逼**：observing 状态下后验限制在 [0.05, 0.90]。触边即说明该判定了（见 §7），不允许在 observing 里表达确定性。
4. **强制反问**：每周复核每条假设必须回答「**什么新信息会把后验打到 0.2 以下？**」写入周报。答不出来 = 假设不可证伪 = 标记 expired 候选。
5. **重算而非增量**：后验永远 = `sigmoid(prior_log_odds + Σ log_lr)`，由 compute-metrics.py 从账本全量重算。**LLM 只给证据定 tier + 标签，不做任何算术**。这同时消灭算术错误和对上周后验措辞的锚定。

## 7. 状态迁移规则

| 迁移 | 触发条件 |
|---|---|
| → `confirmed` | 全部 required 验证条件 status=met，且账本重算后验 ≥ 0.75 |
| → `falsified` | 任一 required 条件的 falsify_if 触发（status=missed），**或**后验 ≤ 0.10 连续两周 |
| → `expired` | review_deadline 到期仍未 confirmed/falsified。按到期时 required 条件实际达成情况判 resolution.outcome（hit/miss），用于评分 |
| `confirmed` → `promoted` | **仅用户手动指令**。skill 只在周报给 promote 建议清单，绝不自动改 ai-investment-weekly 的 portfolio.json |

关闭时必须填 `resolution`：outcome（hit = required 条件达成 / miss = 未达成）、resolved_at、post_mortem 一句话（判断错在哪/对在哪），Brier 与收益字段由 compute-metrics.py 填。

## 8. 每日 vs 每周的分工（重要）

- **每日只记账**：新证据一律 `"pending": true` 入账，不评 LR、不动后验。
- **每周才判案**：weekly-review 统一聚类、评级、定标签，然后跑 compute-metrics.py 重算。
- **唯一例外（T1 紧急通道）**：本公司财报/8-K 直接击穿某 required 条件 → 当日评级入账 + compute-metrics.py 重算 + commit 打 `[URGENT]`。

## 9. 季度校准复盘（每季度第一次周复核强制执行）

对照 data/scorecard.json 回答四问，写入当期周报：

1. **先验是否系统性偏高？** 看校准分箱：后验 0.3 档的实际命中率远低于 0.3 → 下调先验映射表或收紧标尺。
2. **哪个 tier 的证据在骗我？** 看 tier_discrimination：某 tier 在 hit 假设与 miss 假设中的平均 log_lr 无差异 → 该 tier 无信息量 → 下调其 LR 上限（如 T3 从 1.5 → 1.2）。
3. **证伪平均耗时几周？** 越短越好——快速证伪是省钱的核心能力。
4. **expired 占比是否过高？** 高 = 验证条件设计得不可检验 → 收紧 daily-scan 的入池纪律。

修订建议落回本文件（用户确认后修改，版本号 +1；hypotheses 各自记录创建时的方法论版本，不追溯重算）。
