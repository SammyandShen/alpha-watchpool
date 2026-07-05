# alpha-watchpool

新闻驱动的投资假设观察池：每日扫描新闻 → serenity-alpha 方法论产出可检验假设入池 → 每日价格/事件轻跟踪 → 每周贝叶斯深度复核 → HTML 看板呈现后验演变。

**所有输出为研究假设跟踪，不是投资建议。**

## 数据真源与职责边界（必须遵守）

| 文件 | 谁写 | 说明 |
|---|---|---|
| `data/hypotheses.json` | LLM（skill）| 假设池真源。每次修改后必须能通过 `scripts/validate-schema.py` |
| `data/scan-log.json` | LLM（daily-scan）| 近 14 天新闻指纹，去重用 |
| `data/company-notes.json` | LLM（daily-scan，新 ticker 入池时）| 公司中文主营业务档案；数字指标禁止写这里 |
| `docs/data/profiles.json` | 脚本 `sync-profiles.py` | 公司财务指标快照（Yahoo），LLM 只读 |
| `data/scorecard.json` | 脚本 `compute-metrics.py` | LLM 只读 |
| `docs/data/prices.json` | 脚本 `sync-prices.py` | LLM 只读 |
| `docs/data/dashboard.js` | 脚本 `build-dashboard.py` | **LLM 禁止手改** |
| `docs/index.html` | 人工维护 | **LLM 禁止改**（除非用户明确要求改看板本身）|
| `docs/reviews/<date>/index.html` | LLM（weekly-review）| 周报叙事，用 FILL 模板 |

## 铁律

1. **LLM 永不手算后验**。后验 = `sigmoid(prior_log_odds + Σ log_lr)`，由 `compute-metrics.py` 重算。LLM 只给证据定 tier + 九档标签（LR 查表）。
2. **每日只记账（pending 证据），每周才判案（贝叶斯更新）**。唯一例外：T1 decisive 事件即时更新并在 commit message 打 `[URGENT]`。
3. **绝不自动修改** `~/Documents/CC/ai-investment-weekly/data/portfolio.json`。promoted 状态迁移仅在用户手动确认后执行。
4. 先验硬上限 0.45；observing 状态后验夹逼 [0.05, 0.90]。
5. 方法论全文见 `skill-weekly-review/references/methodology.md`，修改需用户确认并把 `methodology_version` +1。

## Skill 职责

- `skill-daily-scan`：产生新假设 + 记 pending 证据 + 标记事件。不做贝叶斯更新、不新建重复 ticker 假设（去重规则见其 SKILL.md）。
- `skill-weekly-review`：逐假设贝叶斯更新、状态迁移、写周报。不新建假设。

## 常用命令

```bash
python3 scripts/validate-schema.py    # 校验 hypotheses.json（skill 跑完必须执行）
python3 scripts/sync-prices.py        # 同步活跃 ticker + 基准收盘价
python3 scripts/compute-metrics.py    # 重算 log-odds / 超额收益 / scorecard
python3 scripts/build-dashboard.py    # 重渲染看板数据 docs/data/dashboard.js
bash install-launchd.sh status        # 查定时任务状态
```
