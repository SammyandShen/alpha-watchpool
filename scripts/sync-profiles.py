#!/usr/bin/env python3
"""
sync-profiles.py — 同步观察池内公司的概况与关键财务指标到 docs/data/profiles.json。

- 数据源：Yahoo Finance quoteSummary API（cookie+crumb，服务端可用，零 MCP 依赖）
- 覆盖：hypotheses.json（含归档 30 天内）中出现的所有假设 ticker（不含基准 ETF）
- 指标：市值、PE/PS、营收 TTM、毛利率/净利率、营收增速、现金/负债、52 周区间、
        行业分类、员工数、官网、英文业务描述（longBusinessSummary）
- 失败降级：某 ticker 拉取失败时保留上次快照（带 stale 标记的 fetched_at）

中文主营业务简介不归本脚本管 — LLM 在入池时写 data/company-notes.json，
build-dashboard.py 负责合并两者。

被 daily-scan.sh / weekly-review.sh 在调 claude 之前调用。失败不阻断主流程。
"""

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HYP_PATH = REPO_ROOT / "data" / "hypotheses.json"
ARCHIVE_PATH = REPO_ROOT / "data" / "archive" / "hypotheses-closed.json"
OUT_PATH = REPO_ROOT / "docs" / "data" / "profiles.json"

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
MODULES = "assetProfile,summaryDetail,financialData,defaultKeyStatistics,price,calendarEvents"
QS_URL = "https://query1.finance.yahoo.com/v10/finance/quoteSummary/{ticker}?modules={modules}&crumb={crumb}"
TIMEOUT_SEC = 15


def load_json(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def collect_tickers() -> list[str]:
    seen, out = set(), []
    for src in (load_json(HYP_PATH, {}), load_json(ARCHIVE_PATH, {})):
        for h in src.get("hypotheses", []):
            t = (h.get("ticker") or "").strip().upper()
            if t and t not in seen:
                seen.add(t)
                out.append(t)
    return out


def make_session():
    """cookie + crumb 舞步：fc.yahoo.com 种 cookie → getcrumb。"""
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor())
    opener.addheaders = [("User-Agent", USER_AGENT)]
    try:
        opener.open("https://fc.yahoo.com", timeout=TIMEOUT_SEC)
    except Exception:
        pass  # 返回 404 但已种 cookie
    crumb = opener.open("https://query1.finance.yahoo.com/v1/test/getcrumb",
                        timeout=TIMEOUT_SEC).read().decode().strip()
    if not crumb or "<" in crumb:
        raise RuntimeError("获取 crumb 失败")
    return opener, crumb


def raw(d: dict, *keys):
    """quoteSummary 数值字段是 {raw, fmt}，取 raw。"""
    cur = d
    for k in keys:
        cur = (cur or {}).get(k)
    if isinstance(cur, dict):
        return cur.get("raw")
    return cur


def fetch_profile(opener, crumb: str, ticker: str) -> dict:
    url = QS_URL.format(ticker=urllib.parse.quote(ticker), modules=MODULES,
                        crumb=urllib.parse.quote(crumb))
    payload = json.loads(opener.open(url, timeout=TIMEOUT_SEC).read())
    result = (payload.get("quoteSummary", {}).get("result") or [None])[0]
    if not result:
        raise RuntimeError("empty quoteSummary.result")
    ap = result.get("assetProfile") or {}
    sd = result.get("summaryDetail") or {}
    fd = result.get("financialData") or {}
    ks = result.get("defaultKeyStatistics") or {}
    pr = result.get("price") or {}

    # 下次财报日：Yahoo 常返回上次财报日（未排期时），只保留今天及以后的
    today = datetime.now().strftime("%Y-%m-%d")
    earnings_dates = sorted(
        d.get("fmt") for d in ((result.get("calendarEvents") or {}).get("earnings") or {}).get("earningsDate") or []
        if isinstance(d, dict) and d.get("fmt"))
    next_earnings = next((d for d in earnings_dates if d >= today), None)

    return {
        "next_earnings_date": next_earnings,
        "name": pr.get("longName") or pr.get("shortName"),
        "sector": ap.get("sector"),
        "industry": ap.get("industry"),
        "employees": ap.get("fullTimeEmployees"),
        "website": ap.get("website"),
        "description_en": ap.get("longBusinessSummary"),
        "currency": pr.get("currency"),
        "market_cap": raw(pr, "marketCap"),
        "trailing_pe": raw(sd, "trailingPE"),
        "forward_pe": raw(sd, "forwardPE"),
        "ps_ttm": raw(ks, "priceToSalesTrailing12Months") or raw(sd, "priceToSalesTrailing12Months"),
        "revenue_ttm": raw(fd, "totalRevenue"),
        "revenue_growth": raw(fd, "revenueGrowth"),
        "gross_margin": raw(fd, "grossMargins"),
        "operating_margin": raw(fd, "operatingMargins"),
        "profit_margin": raw(fd, "profitMargins"),
        "total_cash": raw(fd, "totalCash"),
        "total_debt": raw(fd, "totalDebt"),
        "free_cashflow": raw(fd, "freeCashflow"),
        "week52_low": raw(sd, "fiftyTwoWeekLow"),
        "week52_high": raw(sd, "fiftyTwoWeekHigh"),
        "fetched_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
    }


def main() -> int:
    tickers = collect_tickers()
    existing = load_json(OUT_PATH, {}).get("profiles", {})
    if not tickers:
        print("ℹ️ 观察池为空，无需同步公司概况")

    profiles: dict[str, dict] = {}
    failures = []
    opener = crumb = None
    for t in tickers:
        try:
            if opener is None:
                opener, crumb = make_session()
            profiles[t] = fetch_profile(opener, crumb, t)
            mc = profiles[t].get("market_cap")
            print(f"  ✅ {t}: {profiles[t].get('name')} | mktcap={mc/1e9:.2f}B" if mc else f"  ✅ {t}")
        except Exception as e:  # 网络/结构异常都降级保留旧快照
            failures.append((t, str(e)))
            if t in existing:
                profiles[t] = existing[t]
                print(f"  ⚠️ {t}: {e} — 保留上次快照（{existing[t].get('fetched_at')}）")
            else:
                print(f"  ❌ {t}: {e}（无历史快照）")
        time.sleep(0.3)

    out = {
        "$last_updated": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "source": "yahoo-quote-summary",
        "profiles": profiles,
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"💾 写入 {OUT_PATH.relative_to(REPO_ROOT)}（{len(profiles)}/{len(tickers)} 个 ticker）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
