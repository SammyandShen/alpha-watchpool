#!/usr/bin/env python3
"""
sync-prices.py — 同步观察池所有活跃 ticker + 基准 ETF 的日收盘价序列到 docs/data/prices.json。

- 从 data/hypotheses.json 收集：活跃(observing)假设的 ticker + 所有出现过的 benchmark / sector_benchmark
- 已关闭假设：resolved_at + 30 天内仍继续跟踪（复盘窗口），之后停止拉取（已有序列保留）
- 每 ticker 首次出现时回填历史：从该 ticker 最早的 entry_date 拉到今天
- 之后每日增量追加缺失日期
- 数据源：Yahoo Finance chart API（服务端无 CORS，同 ai-investment-weekly/scripts/sync-quotes.py 已验证）

被 daily-scan.sh / weekly-review.sh 在调 claude 之前调用。失败不阻断主流程。
"""

import json
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HYP_PATH = REPO_ROOT / "data" / "hypotheses.json"
ARCHIVE_PATH = REPO_ROOT / "data" / "archive" / "hypotheses-closed.json"
OUT_PATH = REPO_ROOT / "docs" / "data" / "prices.json"

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&period1={p1}&period2={p2}"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
TIMEOUT_SEC = 15
RETENTION_AFTER_CLOSE_DAYS = 30


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def collect_tickers() -> dict[str, str]:
    """返回 {ticker: 最早需要的起始日期 YYYY-MM-DD}，只含仍需跟踪的 ticker。"""
    today = datetime.now().date()
    needed: dict[str, str] = {}

    def add(ticker: str, start: str) -> None:
        t = (ticker or "").strip().upper()
        if not t:
            return
        if t not in needed or start < needed[t]:
            needed[t] = start

    for src in (load_json(HYP_PATH), load_json(ARCHIVE_PATH)):
        for h in src.get("hypotheses", []):
            entry = (h.get("baseline") or {}).get("entry_date") or h.get("created_at")
            if not entry:
                continue
            status = h.get("status")
            if status not in ("observing",):
                resolved = (h.get("resolution") or {}).get("resolved_at")
                if resolved:
                    try:
                        rd = datetime.strptime(resolved, "%Y-%m-%d").date()
                        if (today - rd).days > RETENTION_AFTER_CLOSE_DAYS:
                            continue  # 复盘窗口已过，不再拉取
                    except ValueError:
                        pass
            add(h.get("ticker"), entry)
            bl = h.get("baseline") or {}
            add(bl.get("benchmark") or "SPY", entry)
            if bl.get("sector_benchmark"):
                add(bl["sector_benchmark"], entry)
    return needed


def fetch_series(ticker: str, start: str) -> list[list]:
    """拉 [start-7天, 今天] 的日收盘序列，返回 [[YYYY-MM-DD, close], ...]。
    往前多拉 7 天缓冲：入池日落在周末/假日时仍能取到最近收盘价作基准。"""
    p1 = int((datetime.strptime(start, "%Y-%m-%d") - timedelta(days=7))
             .replace(tzinfo=timezone.utc).timestamp())
    p2 = int((datetime.now(timezone.utc) + timedelta(days=1)).timestamp())
    req = urllib.request.Request(
        CHART_URL.format(ticker=ticker, p1=p1, p2=p2),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        raise RuntimeError("empty chart.result")
    timestamps = result.get("timestamp") or []
    closes = ((result.get("indicators", {}).get("quote") or [{}])[0]).get("close") or []
    series = []
    for ts, c in zip(timestamps, closes):
        if c is None:
            continue
        d = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        series.append([d, round(float(c), 4)])
    if not series:
        raise RuntimeError("no close data")
    return series


def main() -> int:
    needed = collect_tickers()
    existing = load_json(OUT_PATH)
    series_map: dict[str, list] = existing.get("series", {})

    if not needed:
        print("ℹ️ 观察池为空，无需同步价格")
    failures = []
    for ticker, start in sorted(needed.items()):
        have = series_map.get(ticker) or []
        # 增量：已有数据则只从最后日期前 5 天拉（覆盖修正）
        fetch_start = start
        if have:
            last = have[-1][0]
            fetch_start = (datetime.strptime(last, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
        try:
            new = fetch_series(ticker, fetch_start)
        except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError, ValueError, KeyError) as e:
            failures.append((ticker, str(e)))
            print(f"  ❌ {ticker}: {e}")
            continue
        merged = {d: c for d, c in have}
        merged.update({d: c for d, c in new})
        series_map[ticker] = sorted([[d, c] for d, c in merged.items()])
        print(f"  ✅ {ticker}: {len(series_map[ticker])} 个交易日（最新 {series_map[ticker][-1][0]} = {series_map[ticker][-1][1]}）")
        time.sleep(0.25)  # be polite to Yahoo

    out = {
        "$last_updated": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "yahoo-finance-chart",
        "series": series_map,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"💾 写入 {OUT_PATH.relative_to(REPO_ROOT)}（{len(series_map)} 个 ticker，{len(failures)} 个失败）")
    return 0 if not failures or len(failures) < len(needed) else 1


if __name__ == "__main__":
    sys.exit(main())
