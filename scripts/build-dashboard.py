#!/usr/bin/env python3
"""
build-dashboard.py — 把三个数据真源渲染成看板数据文件 docs/data/dashboard.js。

输入：data/hypotheses.json + data/archive/hypotheses-closed.json + docs/data/prices.json + data/scorecard.json
输出：docs/data/dashboard.js（`const DASHBOARD = {...}`，file:// 可直开，无 CORS）

幂等、确定性、全量重渲染。由 daily-scan.sh / weekly-review.sh 在 schema 校验通过后调用。
LLM 禁止手改本脚本的输出文件。
"""

import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HYP_PATH = REPO_ROOT / "data" / "hypotheses.json"
ARCHIVE_PATH = REPO_ROOT / "data" / "archive" / "hypotheses-closed.json"
PRICES_PATH = REPO_ROOT / "docs" / "data" / "prices.json"
SCORECARD_PATH = REPO_ROOT / "data" / "scorecard.json"
REVIEWS_DIR = REPO_ROOT / "docs" / "reviews"
OUT_PATH = REPO_ROOT / "docs" / "data" / "dashboard.js"


def load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print(f"⚠️ {path} 解析失败，用默认值", file=sys.stderr)
        return default


def main() -> int:
    hyp = load(HYP_PATH, {"hypotheses": []})
    archive = load(ARCHIVE_PATH, {"hypotheses": []})
    prices = load(PRICES_PATH, {"series": {}})
    scorecard = load(SCORECARD_PATH, None)

    all_hyps = hyp.get("hypotheses", []) + archive.get("hypotheses", [])

    # 只带看板需要的 ticker 序列（假设 ticker + 各自基准）
    needed = set()
    for h in all_hyps:
        needed.add(h.get("ticker"))
        bl = h.get("baseline") or {}
        needed.add(bl.get("benchmark") or "SPY")
        if bl.get("sector_benchmark"):
            needed.add(bl["sector_benchmark"])
    series = {t: s for t, s in (prices.get("series") or {}).items() if t in needed}

    reviews = sorted(
        [d.name for d in REVIEWS_DIR.iterdir() if d.is_dir() and (d / "index.html").exists()],
        reverse=True,
    ) if REVIEWS_DIR.exists() else []

    payload = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "methodology_version": hyp.get("$methodology_version"),
        "hypotheses": all_hyps,
        "prices": series,
        "scorecard": scorecard,
        "reviews": reviews,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(
        "// 由 scripts/build-dashboard.py 自动生成 — 禁止手改\n"
        "const DASHBOARD = " + json.dumps(payload, ensure_ascii=False) + ";\n",
        encoding="utf-8",
    )
    active = sum(1 for h in all_hyps if h.get("status") == "observing")
    print(f"✅ dashboard.js 已生成：{len(all_hyps)} 条假设（{active} 活跃），{len(series)} 个价格序列，{len(reviews)} 篇周报")
    return 0


if __name__ == "__main__":
    sys.exit(main())
