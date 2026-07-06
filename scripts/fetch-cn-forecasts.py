#!/usr/bin/env python3
"""
fetch-cn-forecasts.py — 拉取近期 A 股业绩预告（结构化 T1 信号源）→ data/cn-forecasts.json。

- 数据源：东方财富数据中心 RPT_PUBLIC_OP_NEWPREDICT（公开接口，服务端可用，零 MCP 依赖）
- 范围：近 LOOKBACK_DAYS 天披露的 预增/略增/扭亏/续盈 预告（利好向；预减/首亏不作为候选源，
  但池内活跃 ticker 的利空预告单独检出，供 T1 紧急通道判断）
- 输出按「扣非净利增幅下限」降序，daily-scan 直接读此文件作为 A 股候选 feed，
  WebSearch 只做补充与交叉验证

被 daily-scan.sh 在调 claude 之前调用。失败不阻断主流程（保留上次快照）。
"""

import json
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HYP_PATH = REPO_ROOT / "data" / "hypotheses.json"
OUT_PATH = REPO_ROOT / "data" / "cn-forecasts.json"

API = "https://datacenter-web.eastmoney.com/api/data/v1/get"
HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://data.eastmoney.com/"}
LOOKBACK_DAYS = 7
POSITIVE_TYPES = ("预增", "略增", "扭亏", "续盈")
NEGATIVE_TYPES = ("预减", "略减", "首亏", "续亏")
PAGE_SIZE = 100
MAX_PAGES = 5
TIMEOUT_SEC = 20


def yahoo_ticker(code: str) -> str:
    """东财证券代码 → Yahoo 后缀格式。6xxxx→.SS，0/3xxxx→.SZ，4/8/9(北交所新三板)→.BJ"""
    if code.startswith("6"):
        return f"{code}.SS"
    if code[0] in "03":
        return f"{code}.SZ"
    return f"{code}.BJ"


def fetch_page(filter_expr: str, page: int) -> list[dict]:
    params = {
        "reportName": "RPT_PUBLIC_OP_NEWPREDICT",
        "columns": "ALL",
        "pageSize": str(PAGE_SIZE),
        "pageNumber": str(page),
        "sortColumns": "NOTICE_DATE",
        "sortTypes": "-1",
        "filter": filter_expr,
    }
    req = urllib.request.Request(API + "?" + urllib.parse.urlencode(params), headers=HEADERS)
    payload = json.loads(urllib.request.urlopen(req, timeout=TIMEOUT_SEC).read())
    if not payload.get("success"):
        raise RuntimeError(f"eastmoney success=false: {payload.get('message')}")
    return (payload.get("result") or {}).get("data") or []


def normalize(row: dict) -> dict:
    notice = (row.get("NOTICE_DATE") or "")[:10]
    report = (row.get("REPORT_DATE") or "")[:10]
    return {
        "ticker": yahoo_ticker(row.get("SECURITY_CODE", "")),
        "name": row.get("SECURITY_NAME_ABBR"),
        "notice_date": notice,
        "report_period": report,
        "predict_type": row.get("PREDICT_TYPE"),
        "metric": row.get("PREDICT_FINANCE"),
        "increase_pct_lower": row.get("PREDICT_RATIO_LOWER") or row.get("INCREASE_JZ"),
        "increase_pct_upper": row.get("PREDICT_RATIO_UPPER"),
        "amount_lower_wan": row.get("PREDICT_AMT_LOWER"),
        "amount_upper_wan": row.get("PREDICT_AMT_UPPER"),
        "content": row.get("PREDICT_CONTENT"),
        "reason": row.get("CHANGE_REASON_EXPLAIN"),
    }


def main() -> int:
    since = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y-%m-%d")
    active_tickers = set()
    if HYP_PATH.exists():
        d = json.loads(HYP_PATH.read_text(encoding="utf-8"))
        active_tickers = {h.get("ticker") for h in d.get("hypotheses", [])
                          if h.get("status") == "observing"}

    try:
        # 利好向预告（候选 feed）
        pos_filter = f'(PREDICT_TYPE in ({",".join(chr(34)+t+chr(34) for t in POSITIVE_TYPES)}))(NOTICE_DATE>=\'{since}\')'
        positives: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            rows = fetch_page(pos_filter, page)
            positives.extend(normalize(r) for r in rows)
            if len(rows) < PAGE_SIZE:
                break
        # IS_LATEST 已含在源数据排序里；同一代码同期多条取最新披露
        seen: set[tuple] = set()
        deduped = []
        for p in positives:
            key = (p["ticker"], p["report_period"], p["metric"])
            if key in seen:
                continue
            seen.add(key)
            deduped.append(p)
        deduped.sort(key=lambda x: (x.get("increase_pct_lower") or 0), reverse=True)

        # 池内活跃 ticker 的利空预告（T1 紧急通道线索）
        neg_filter = f'(PREDICT_TYPE in ({",".join(chr(34)+t+chr(34) for t in NEGATIVE_TYPES)}))(NOTICE_DATE>=\'{since}\')'
        neg_rows = [normalize(r) for r in fetch_page(neg_filter, 1)]
        pool_warnings = [r for r in neg_rows if r["ticker"] in active_tickers]

    except Exception as e:
        print(f"❌ 东财接口失败: {e}", file=sys.stderr)
        if OUT_PATH.exists():
            print("ℹ️ 保留上次快照")
            return 0
        return 1

    out = {
        "$generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "$source": "eastmoney RPT_PUBLIC_OP_NEWPREDICT",
        "$lookback_days": LOOKBACK_DAYS,
        "positive_forecasts": deduped,
        "pool_negative_warnings": pool_warnings,
    }
    OUT_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"💾 业绩预告：利好 {len(deduped)} 条（近 {LOOKBACK_DAYS} 天），池内利空警告 {len(pool_warnings)} 条")
    if deduped[:3]:
        for p in deduped[:3]:
            print(f"   · {p['name']}({p['ticker']}) {p['predict_type']} {p['metric']} +{p['increase_pct_lower']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
