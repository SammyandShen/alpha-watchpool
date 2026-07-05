---
name: alpha-weekly-review
description: 每周对观察池（data/hypotheses.json）逐假设做贝叶斯深度复核：聚合本周 pending 证据、定向补搜、按九档似然比标尺定级、状态迁移判定（confirmed/falsified/expired）、生成周报 HTML。当用户说「跑周度假设复核」「weekly review」「复核观察池」或由 launchd 周六定时触发时使用。不新建假设（那是 alpha-daily-scan 的职责）。
---

# Alpha Weekly Review（每周贝叶斯深度复核）

## 职责边界

**做**：证据评级、贝叶斯更新（通过脚本）、状态迁移、周报叙事、promote 建议。
**不做**：新建假设；手算任何数字；自动 promote。

先读 `references/methodology.md` 全文——所有评级和迁移决策以它为准。

## 流程

### 0. 准备数字（脚本先行，LLM 不算术）

```bash
python3 scripts/sync-prices.py        # 若 shell 没跑过
python3 scripts/compute-metrics.py    # 生成 data/review-input.json + data/scorecard.json
```

读取：
- `data/hypotheses.json` — 假设全文
- `data/review-input.json` — 每假设的超额收益、账本重算 log-odds、价格证据建议标签、deadline 倒计时、pending 证据清单
- `data/scorecard.json` — 系统评分（周报摘要用）

### 1. 逐假设复核（对每条 status=observing）

1. **补搜**：围绕该假设 validation_conditions 的 metric 做 1-2 次定向 WebSearch（不是泛搜 ticker 新闻——搜「<company> <指标关键词> quarterly」这类能直接命中验证条件的查询）。有 FMP MCP 时优先查财报数据/transcript。
2. **证据聚类**：本周 pending 证据 + 补搜新证据放在一起，同一底层事件合并——首条保留，其余 `correlates_with` 指向它、标签强制 neutral。
3. **逐条定级**：按 methodology.md §2-§3，先定 tier，再用鉴别力问句选九档标签，lr/log_lr 查表填写，`"pending": false`。
4. **价格证据**：`review-input.json` 给了建议标签。若非 neutral 且本周尚无价格证据，追加一条 `type: "P_price"` 证据（标签只能采纳或降级）。
5. **验证条件核对**：到期（check_by ≤ 今天）的条件根据实际数据判 met / missed / unclear；未到期但已有决定性数据的也可判。

### 2. 重算与迁移

```bash
python3 scripts/compute-metrics.py --append-history "weekly review <YYYY-MM-DD>"
```

然后按 methodology.md §7 判定状态迁移。迁移时：
- 更新 `status` + `status_history`（带 reason）
- 关闭的假设填 `resolution.outcome`（hit/miss）、`resolved_at`、`post_mortem` 一句话
- 数字字段（brier、收益）留给 compute-metrics.py，再跑一次即可填充

### 3. 强制反问

每条 observing 假设写一句：「什么新信息会把后验打到 0.2 以下」。答不出来 → 标 expired 候选，写进周报警示区。

### 4. 生成周报

用 `assets/review-template.html` 模板（FILL 标记替换），写到 `docs/reviews/<YYYY-MM-DD>/index.html`：

1. 池子总览：本周新增/关闭/迁移，平均后验变化
2. 逐假设明细：证据 → tier/标签 → 后验前后（数字来自重算结果，不自己算）
3. 状态迁移公告 + post_mortem
4. **promote 建议清单**（若有 confirmed）：明确写「需用户手动确认，本系统不自动建仓」
5. 每条假设的证伪触发条件（§3 的反问答案）
6. 评分卡摘要（读 scorecard.json：命中率、Brier、校准）
7. **每季度第一次周复核**：追加校准复盘章（methodology.md §9 四问）

### 5. 落盘 + 自检

1. `data/hypotheses.json` 的 `$last_updated` 改今天
2. `python3 scripts/validate-schema.py` **必须通过**，报错修复后重跑
3. 终端输出简短总结：几条复核、几条迁移、后验变化 top3、周报路径

## 禁止事项

- 禁止手算后验/log_odds/收益/Brier——一切数字来自 compute-metrics.py 输出
- 禁止新建假设（发现新机会记到周报「下周扫描线索」区，让 daily-scan 处理）
- 禁止自动修改 ai-investment-weekly/data/portfolio.json
- 单假设单周 |Σ Δlog-odds| ≤ 2.2（methodology.md §6.2），超了要主动降级证据
