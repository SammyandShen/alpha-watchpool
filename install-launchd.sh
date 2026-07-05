#!/bin/bash
# install-launchd.sh — 安装/卸载/测试 alpha-watchpool 的两个定时任务
#   daily   = 工作日 08:00 每日扫描（skill: alpha-daily-scan）
#   weekly  = 周六 12:00 深度复核（skill: alpha-weekly-review）
#   all     = 两个都操作（默认）
#
# 用法：
#   bash install-launchd.sh [install|test|status|remove] [daily|weekly|all]
#
# 示例：
#   bash install-launchd.sh                  # = install all
#   bash install-launchd.sh test daily       # 立即跑一次每日扫描（全链路）
#   bash install-launchd.sh status           # 查看任务状态
#   bash install-launchd.sh remove all       # 卸载

set -e

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

declare_task() {
    case "$1" in
        daily)
            PLIST_NAME="com.user.alpha-daily-scan"
            LABEL="com.user.alpha-daily-scan"
            SCRIPT="daily-scan.sh"
            DESC="工作日 08:00 · 每日 alpha 扫描"
            ;;
        weekly)
            PLIST_NAME="com.user.alpha-weekly-review"
            LABEL="com.user.alpha-weekly-review"
            SCRIPT="weekly-review.sh"
            DESC="周六 12:00 · 假设深度复核"
            ;;
        *)
            echo "❌ 未知任务名: $1（应为 daily / weekly）" >&2
            exit 1
            ;;
    esac
    PLIST_SOURCE="$PROJECT_DIR/launchd/$PLIST_NAME.plist"
    PLIST_DEST="$HOME/Library/LaunchAgents/$PLIST_NAME.plist"
    SCRIPT_PATH="$PROJECT_DIR/$SCRIPT"
}

install_one() {
    declare_task "$1"
    echo ""
    echo "📦 安装：$DESC"
    if [ ! -f "$PLIST_SOURCE" ]; then echo "❌ 找不到模板 $PLIST_SOURCE"; exit 1; fi
    if [ ! -f "$SCRIPT_PATH" ]; then echo "❌ 找不到脚本 $SCRIPT_PATH"; exit 1; fi

    mkdir -p "$HOME/Library/LaunchAgents"
    sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
        -e "s|__HOME__|$HOME|g" \
        "$PLIST_SOURCE" > "$PLIST_DEST"
    chmod +x "$SCRIPT_PATH"

    # bootout/bootstrap（不用旧的 load/unload）— macOS 13+ TCC 兼容
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST_DEST"
    echo "✅ 已加载：$PLIST_DEST"
}

remove_one() {
    declare_task "$1"
    echo ""
    echo "🗑 卸载：$DESC"
    if [ -f "$PLIST_DEST" ]; then
        launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && echo "✅ 已卸载" || echo "ℹ️ 任务未在运行"
        rm -f "$PLIST_DEST"
    else
        echo "ℹ️ 没有这个任务的 plist"
    fi
}

test_one() {
    declare_task "$1"
    echo ""
    echo "🧪 立即跑一次：$DESC"
    cd "$PROJECT_DIR"
    bash "$SCRIPT"
}

status_one() {
    declare_task "$1"
    echo ""
    echo "── $DESC ──"
    if launchctl list | grep -q "$LABEL"; then
        launchctl list | grep "$LABEL"
        echo "✅ 已加载"
    else
        echo "❌ 未加载（运行 'bash install-launchd.sh install $1' 安装）"
    fi
}

ACTION="${1:-install}"
TARGET="${2:-all}"

case "$ACTION" in install|test|status|remove) ;; *)
    echo "❌ 未知动作: $ACTION"
    echo "用法: bash install-launchd.sh [install|test|status|remove] [daily|weekly|all]"
    exit 1
    ;;
esac

case "$TARGET" in
    daily|weekly) TASKS="$TARGET" ;;
    all) TASKS="daily weekly" ;;
    *) echo "❌ 未知任务: $TARGET（应为 daily / weekly / all）" >&2; exit 1 ;;
esac

if [ "$ACTION" = "test" ] && [ "$TARGET" = "all" ]; then
    echo "⚠️ test 模式只能跑一个任务，请指定 daily / weekly"
    exit 1
fi

case "$ACTION" in
    install)
        for t in $TASKS; do install_one "$t"; done
        echo ""
        echo "📋 下一步："
        echo "  · 立即跑一次：  bash install-launchd.sh test daily"
        echo "                  bash install-launchd.sh test weekly"
        echo "  · 查看状态：    bash install-launchd.sh status"
        echo "  · 看日志：      tail -f $PROJECT_DIR/logs/\$(date +%Y-%m-%d)-scan.log"
        ;;
    remove)
        for t in $TASKS; do remove_one "$t"; done
        ;;
    test)
        test_one "$TASKS"
        ;;
    status)
        for t in $TASKS; do status_one "$t"; done
        echo ""
        echo "最近一次执行日志："
        LATEST_LOG=$(ls -t "$PROJECT_DIR/logs/"*.log 2>/dev/null | head -1)
        if [ -n "$LATEST_LOG" ]; then
            echo "($LATEST_LOG)"
            tail -10 "$LATEST_LOG"
        else
            echo "（还没有日志）"
        fi
        ;;
esac
