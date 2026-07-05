# 🔬 Alpha Watchpool

新闻驱动的投资假设观察池：把 serenity-alpha 的新闻分析变成**可追责的闭环系统**。

```
每日 08:00（工作日）                        每周六 12:00
┌─────────────────────┐                ┌──────────────────────┐
│ daily-scan.sh        │                │ weekly-review.sh      │
│  ① sync-prices.py    │                │  ① sync-prices.py     │
│  ② claude:           │                │     compute-metrics.py│
│     alpha-daily-scan │                │  ② claude:            │
│     新闻→假设入池     │                │     alpha-weekly-review│
│     pending 证据记账  │                │     贝叶斯评级+状态迁移│
│  ③ validate-schema   │                │  ③ validate-schema    │
│  ④ compute-metrics   │                │  ④ compute-metrics    │
│     build-dashboard  │                │     build-dashboard   │
│  ⑤ git push          │                │  ⑤ git push + 周报    │
└─────────────────────┘                └──────────────────────┘
            ↓                                     ↓
        data/hypotheses.json（真源） → docs/index.html（看板）
```

## 核心思想

1. **命题可判**：每条假设 = 「required 验证条件在 1-4 个季度内达成」，不是「股价会涨」
2. **贝叶斯记账**：先验（serenity 七维评分映射，上限 0.45）+ 证据似然比（九档语言标尺查表）→ 后验由脚本重算，LLM 永不手算
3. **每日记账、每周判案**：日常证据 pending 入账不动后验，防噪音漂移
4. **判断与赚钱分开评分**：命中率/Brier/校准曲线（轨道 A）与超额收益（轨道 B）独立记账，季度校准复盘让方法论自我修正

方法论全文：[skill-weekly-review/references/methodology.md](skill-weekly-review/references/methodology.md)

## 手动运行

```bash
bash install-launchd.sh              # 安装两个定时任务
bash install-launchd.sh test daily   # 立即跑一次每日扫描
bash install-launchd.sh test weekly  # 立即跑一次周度复核
open docs/index.html                 # 本地看板（file:// 可直开）
```

交互式会话里也可以直接说「跑今天的 alpha 扫描」/「跑周度假设复核」。

## 免责声明

本仓库为个人研究假设跟踪工具。所有概率为研究性主观估计，所有输出不构成投资建议。
