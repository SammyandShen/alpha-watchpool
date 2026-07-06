#!/bin/bash
# daily-scan.sh
# 工作日 08:00（中国时间）跑一次：价格同步 → claude skill alpha-daily-scan → 校验 → 渲染看板 → push
# 错开 07:00 的 ai-investment-weekly daily-briefing，避免两个 claude 并发。

set -e

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CLAUDE_CMD="claude"
GIT_BRANCH="main"

LOG_DIR="$PROJECT_DIR/logs"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/$TODAY-scan.log"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "================================================"
echo "🔍 Alpha Daily Scan · $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "================================================"

# 周末跳过（launchd 已只配工作日，双保险）
DOW=$(date +%u)
if [ "$DOW" -ge 6 ]; then
    echo "今天是周末（DOW=$DOW），跳过扫描。"
    exit 0
fi

cd "$PROJECT_DIR"
source "$HOME/.zshrc" 2>/dev/null || true
source "$HOME/.bash_profile" 2>/dev/null || true
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
unset ANTHROPIC_API_KEY   # 走 Pro 订阅

if ! command -v "$CLAUDE_CMD" &> /dev/null; then echo "❌ 找不到 claude"; exit 1; fi
if ! command -v git &> /dev/null; then echo "❌ 找不到 git"; exit 1; fi
echo "✅ claude / git OK"

echo ""
echo "💹 ① 价格快照 + 公司概况 + A股业绩预告..."
python3 scripts/sync-prices.py || echo "⚠️ 价格同步失败（继续执行，明日会补拉）"
python3 scripts/sync-profiles.py || echo "⚠️ 公司概况同步失败（看板显示上次快照）"
python3 scripts/fetch-cn-forecasts.py || echo "⚠️ 业绩预告抓取失败（A股信号回退到 WebSearch）"

echo ""
echo "🤖 ② 调用 skill alpha-daily-scan..."
"$CLAUDE_CMD" --print --permission-mode bypassPermissions "请按当前项目根目录下 skill-daily-scan/SKILL.md 的指引，执行今天（$TODAY）的每日扫描。要求：
1. 严格遵守 SKILL.md 的流程与去重规则；serenity 分析方法论读 ~/.claude/skills/serenity-alpha/SKILL.md，字段映射读 skill-daily-scan/references/serenity-pipeline.md。
2. headless 环境下没有 FMP MCP 工具，直接用 WebSearch 做新闻扫描。
3. 完成后运行 python3 scripts/validate-schema.py，必须通过；不通过就修复到通过为止。
4. 最后输出简短总结（新建/追加/跳过统计）。若触发 T1 紧急通道，总结第一行写 URGENT: <ticker> <事件>。"

echo ""
echo "🛡 ③ schema 校验（护栏）..."
if ! python3 scripts/validate-schema.py; then
    echo "❌ 校验失败 — 回滚 data/，本次不发布"
    git checkout -- data/ 2>/dev/null || true
    osascript -e "display notification \"schema 校验失败，已回滚\" with title \"🔍 Alpha Daily Scan\" sound name \"Basso\"" 2>/dev/null || true
    exit 1
fi

echo ""
echo "📊 ④ 重算指标 + 渲染看板..."
# 补拉当日新入池 ticker 的价格与概况（步骤①跑在 skill 之前，新 ticker 会漏）
python3 scripts/sync-prices.py || true
python3 scripts/sync-profiles.py || true
python3 scripts/compute-metrics.py
python3 scripts/build-dashboard.py

echo ""
echo "📤 ⑤ 提交..."
COMMIT_PREFIX="🔍 Daily scan"
if grep -q "^URGENT:" "$LOG_FILE" 2>/dev/null; then COMMIT_PREFIX="[URGENT] 🔍 Daily scan"; fi
if [[ -n $(git status --porcelain data/ docs/) ]]; then
    git add data/ docs/
    git commit -m "$COMMIT_PREFIX: $TODAY"
    if git remote get-url origin &>/dev/null; then
        git push origin "$GIT_BRANCH" && echo "✅ 已推送" || echo "⚠️ push 失败（本地已提交）"
    else
        echo "ℹ️ 未配置 remote，仅本地提交"
    fi
else
    echo "ℹ️ 无变化，跳过提交"
fi

echo ""
echo "🎉 完成！日志：$LOG_FILE"
osascript -e "display notification \"今日 alpha 扫描完成\" with title \"🔍 Alpha Daily Scan\" sound name \"Glass\"" 2>/dev/null || true
