#!/bin/bash
# weekly-review.sh
# 每周六 12:00（中国时间）跑一次：价格同步 → 指标计算 → claude skill alpha-weekly-review → 校验 → 渲染看板 → push
# 错开周六 10:00 的 ai-investment-weekly weekend-review，避免两个 claude 并发。

set -e

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
CLAUDE_CMD="claude"
GIT_BRANCH="main"

LOG_DIR="$PROJECT_DIR/logs"
TODAY=$(date +%Y-%m-%d)
LOG_FILE="$LOG_DIR/$TODAY-review.log"

mkdir -p "$LOG_DIR"
exec > >(tee -a "$LOG_FILE") 2>&1

echo "================================================"
echo "🔬 Alpha Weekly Review · $(date '+%Y-%m-%d %H:%M:%S %Z')"
echo "================================================"

cd "$PROJECT_DIR"
source "$HOME/.zshrc" 2>/dev/null || true
source "$HOME/.bash_profile" 2>/dev/null || true
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
unset ANTHROPIC_API_KEY   # 走 Pro 订阅

if ! command -v "$CLAUDE_CMD" &> /dev/null; then echo "❌ 找不到 claude"; exit 1; fi
echo "✅ claude / git OK"

# 池子为空则跳过（省一次 claude 调用）
ACTIVE=$(python3 -c "import json;print(sum(1 for h in json.load(open('data/hypotheses.json'))['hypotheses'] if h['status']=='observing'))")
if [ "$ACTIVE" = "0" ]; then
    echo "ℹ️ 观察池无活跃假设，跳过本周复核。"
    exit 0
fi

echo ""
echo "💹 ① 价格快照 + 指标预计算..."
python3 scripts/sync-prices.py || echo "⚠️ 价格同步失败（用上次快照继续）"
python3 scripts/compute-metrics.py

echo ""
echo "🤖 ② 调用 skill alpha-weekly-review..."
"$CLAUDE_CMD" --print --permission-mode bypassPermissions "请按当前项目根目录下 skill-weekly-review/SKILL.md 的指引，执行本周（$TODAY）的假设深度复核。要求：
1. 先通读 skill-weekly-review/references/methodology.md，所有证据评级与状态迁移以它为准。
2. 数字一律来自 data/review-input.json 和 compute-metrics.py 的输出，禁止手算。
3. 评级完成后运行 python3 scripts/compute-metrics.py --append-history \"weekly review $TODAY\" 重算后验。
4. 周报写到 docs/reviews/$TODAY/index.html（模板：skill-weekly-review/assets/review-template.html）。
5. 完成后运行 python3 scripts/validate-schema.py，必须通过。
6. 最后输出简短总结：几条复核、几条状态迁移、后验变化 top3。"

echo ""
echo "🛡 ③ schema 校验（护栏）..."
if ! python3 scripts/validate-schema.py; then
    echo "❌ 校验失败 — 回滚 data/，本次不发布"
    git checkout -- data/ 2>/dev/null || true
    osascript -e "display notification \"schema 校验失败，已回滚\" with title \"🔬 Alpha Weekly Review\" sound name \"Basso\"" 2>/dev/null || true
    exit 1
fi

echo ""
echo "📊 ④ 终算指标 + 渲染看板..."
python3 scripts/compute-metrics.py
python3 scripts/build-dashboard.py

echo ""
echo "📤 ⑤ 提交..."
if [[ -n $(git status --porcelain data/ docs/) ]]; then
    git add data/ docs/
    git commit -m "🔬 Weekly review: $TODAY"
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
osascript -e "display notification \"本周假设复核完成\" with title \"🔬 Alpha Weekly Review\" sound name \"Glass\"" 2>/dev/null || true
