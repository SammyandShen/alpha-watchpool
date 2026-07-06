#!/usr/bin/env python3
"""
compute-metrics.py — 系统的算术引擎。LLM 永不手算，所有数字由本脚本产生。

做四件事：
1. 账本重算（权威）：每条假设 current.log_odds = prior.log_odds + Σ(非 pending 证据 log_lr)，
   current.p = sigmoid(log_odds)，写回 hypotheses.json。LLM 拍脑姍改的后验会被无条件纠正。
2. 复核输入表：每条活跃假设的入池以来收益、vs SPY / 行业基准超额、价格证据建议标签、
   各验证条件 deadline 倒计时 → data/review-input.json（weekly-review skill 只读它，不自己算）。
3. 评分卡：命中率、Brier（最终 + 时间平均）、校准分箱、按 tier 鉴别力、经济结果 → data/scorecard.json。
4. 归档：关闭满 30 天的假设迁入 data/archive/hypotheses-closed.json。

用法：
  python3 scripts/compute-metrics.py                       # 全量重算 + 输出
  python3 scripts/compute-metrics.py --append-history "weekly review W3"
                                                           # 额外给每条活跃假设的 posterior_history 追加今日点
"""

import argparse
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HYP_PATH = REPO_ROOT / "data" / "hypotheses.json"
ARCHIVE_PATH = REPO_ROOT / "data" / "archive" / "hypotheses-closed.json"
PRICES_PATH = REPO_ROOT / "docs" / "data" / "prices.json"
REVIEW_INPUT_PATH = REPO_ROOT / "data" / "review-input.json"
SCORECARD_PATH = REPO_ROOT / "data" / "scorecard.json"

ARCHIVE_AFTER_DAYS = 30
TODAY = datetime.now().strftime("%Y-%m-%d")


def sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def load(path: Path, default: dict) -> dict:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def price_at(series: list, date: str, direction: str = "on_or_before") -> float | None:
    """series: [[date, close]...] 升序。取 date 当天或之前最近一个收盘价。"""
    best = None
    for d, c in series or []:
        if d <= date:
            best = c
        else:
            break
    return best


def latest_price(series: list) -> tuple[str, float] | None:
    if not series:
        return None
    return series[-1][0], series[-1][1]


def pct(a: float, b: float) -> float | None:
    if not a or not b:
        return None
    return round((b / a - 1) * 100, 2)


def price_evidence_suggestion(excess: float | None) -> str:
    """§3.4 映射：excess(%) → 建议标签。LLM 只能采纳或降级，不能升级。"""
    if excess is None:
        return "neutral"
    if excess >= 30:
        return "moderate_support"
    if excess >= 15:
        return "weak_support"
    if excess <= -30:
        return "moderate_against"
    if excess <= -15:
        return "weak_against"
    return "neutral"


def recompute_ledger(h: dict) -> tuple[float, float]:
    prior_lo = (h.get("prior") or {}).get("log_odds") or 0.0
    s = 0.0
    for ev in h.get("evidence_log") or []:
        if ev.get("pending"):
            continue
        llr = (ev.get("likelihood") or {}).get("log_lr")
        if isinstance(llr, (int, float)):
            s += llr
    lo = prior_lo + s
    return round(lo, 4), round(sigmoid(lo), 4)


def returns_block(h: dict, series_map: dict) -> dict:
    bl = h.get("baseline") or {}
    t = h.get("ticker")
    entry_date = bl.get("entry_date")
    out = {"ticker_return_pct": None, "spy_return_pct": None, "sector_return_pct": None,
           "excess_vs_spy_pct": None, "excess_vs_sector_pct": None, "as_of": None}

    tser = series_map.get(t)
    if not tser or not entry_date:
        return out
    t0 = bl.get("entry_price") or price_at(tser, entry_date)
    lat = latest_price(tser)
    if not t0 or not lat:
        return out
    as_of, t1 = lat
    out["as_of"] = as_of
    out["ticker_return_pct"] = pct(t0, t1)

    for key, field in (("benchmark", "spy_return_pct"), ("sector_benchmark", "sector_return_pct")):
        b = bl.get(key)
        bser = series_map.get(b) if b else None
        if not bser:
            continue
        b0 = bl.get(f"{key}_entry_price") or price_at(bser, entry_date)
        b1 = price_at(bser, as_of)
        if b0 and b1:
            out[field] = pct(b0, b1)

    if out["ticker_return_pct"] is not None and out["spy_return_pct"] is not None:
        out["excess_vs_spy_pct"] = round(out["ticker_return_pct"] - out["spy_return_pct"], 2)
    if out["ticker_return_pct"] is not None and out["sector_return_pct"] is not None:
        out["excess_vs_sector_pct"] = round(out["ticker_return_pct"] - out["sector_return_pct"], 2)
    return out


def brier_scores(h: dict) -> tuple[float | None, float | None]:
    outcome = (h.get("resolution") or {}).get("outcome")
    if outcome not in ("hit", "miss"):
        return None, None
    y = 1.0 if outcome == "hit" else 0.0
    ph = h.get("posterior_history") or []
    if not ph:
        return None, None
    final = round((ph[-1]["p"] - y) ** 2, 4)
    avg = round(sum((pt["p"] - y) ** 2 for pt in ph) / len(ph), 4)
    return final, avg


def build_scorecard(active: list, archived: list, series_map: dict) -> dict:
    resolved = [h for h in active + archived
                if (h.get("resolution") or {}).get("outcome") in ("hit", "miss")]
    hits = [h for h in resolved if h["resolution"]["outcome"] == "hit"]

    # 校准分箱：所有已判定假设的每个周度后验点
    bins = [{"range": f"{i/10:.1f}-{(i+2)/10:.1f}", "lo": i / 10, "hi": (i + 2) / 10,
             "n": 0, "hits": 0} for i in range(0, 10, 2)]
    for h in resolved:
        y = 1 if h["resolution"]["outcome"] == "hit" else 0
        for pt in h.get("posterior_history") or []:
            for b in bins:
                if b["lo"] <= pt["p"] < b["hi"] or (b["hi"] == 1.0 and pt["p"] == 1.0):
                    b["n"] += 1
                    b["hits"] += y
                    break
    for b in bins:
        b["actual_rate"] = round(b["hits"] / b["n"], 3) if b["n"] else None
        del b["lo"], b["hi"]

    # 按 tier 鉴别力：confirmed vs falsified 假设中各 tier 的平均 log_lr
    tier_disc: dict[str, dict] = {}
    for h in resolved:
        grp = "hit" if h["resolution"]["outcome"] == "hit" else "miss"
        for ev in h.get("evidence_log") or []:
            if ev.get("pending"):
                continue
            llr = (ev.get("likelihood") or {}).get("log_lr")
            if not isinstance(llr, (int, float)):
                continue
            d = tier_disc.setdefault(ev.get("type", "?"), {"hit": [], "miss": []})
            d[grp].append(llr)
    tier_summary = {}
    for tier, d in tier_disc.items():
        tier_summary[tier] = {
            "avg_loglr_in_hits": round(sum(d["hit"]) / len(d["hit"]), 3) if d["hit"] else None,
            "avg_loglr_in_misses": round(sum(d["miss"]) / len(d["miss"]), 3) if d["miss"] else None,
            "n_hit": len(d["hit"]), "n_miss": len(d["miss"]),
        }

    # 经济结果：分状态
    econ: dict[str, dict] = {}
    for h in active + archived:
        st = h.get("status")
        rb = returns_block(h, series_map)
        if rb["ticker_return_pct"] is None:
            continue
        g = econ.setdefault(st, {"n": 0, "sum_ret": 0.0, "sum_excess_spy": 0.0, "n_excess": 0})
        g["n"] += 1
        g["sum_ret"] += rb["ticker_return_pct"]
        if rb["excess_vs_spy_pct"] is not None:
            g["sum_excess_spy"] += rb["excess_vs_spy_pct"]
            g["n_excess"] += 1
    econ_summary = {st: {
        "n": g["n"],
        "avg_return_pct": round(g["sum_ret"] / g["n"], 2),
        "avg_excess_vs_spy_pct": round(g["sum_excess_spy"] / g["n_excess"], 2) if g["n_excess"] else None,
    } for st, g in econ.items()}

    briers = [b for h in resolved for b in [brier_scores(h)[0]] if b is not None]
    briers_avg = [b for h in resolved for b in [brier_scores(h)[1]] if b is not None]

    return {
        "$generated_at": TODAY,
        "counts": {
            "active": sum(1 for h in active if h.get("status") == "observing"),
            "confirmed": sum(1 for h in active + archived if h.get("status") == "confirmed"),
            "falsified": sum(1 for h in active + archived if h.get("status") == "falsified"),
            "expired": sum(1 for h in active + archived if h.get("status") == "expired"),
            "promoted": sum(1 for h in active + archived if h.get("status") == "promoted"),
        },
        "prediction_quality": {
            "hit_rate": round(len(hits) / len(resolved), 3) if resolved else None,
            "n_resolved": len(resolved),
            "brier_final_mean": round(sum(briers) / len(briers), 4) if briers else None,
            "brier_time_avg_mean": round(sum(briers_avg) / len(briers_avg), 4) if briers_avg else None,
            "calibration_bins": bins,
            "tier_discrimination": tier_summary,
        },
        "economics": econ_summary,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--append-history", metavar="NOTE", default=None,
                    help="给每条活跃假设 posterior_history 追加今日重算点（weekly review / URGENT 用）")
    args = ap.parse_args()

    data = load(HYP_PATH, {"hypotheses": []})
    archive = load(ARCHIVE_PATH, {"hypotheses": []})
    prices = load(PRICES_PATH, {"series": {}})
    series_map = prices.get("series", {})

    # 0. 基准入池价回填（skill 入池时 prices.json 可能还没有该 ticker/基准的数据）
    for h in data["hypotheses"]:
        bl = h.get("baseline") or {}
        ed = bl.get("entry_date")
        if not ed:
            continue
        for pk, tk in (("entry_price", h.get("ticker")),
                       ("benchmark_entry_price", bl.get("benchmark")),
                       ("sector_benchmark_entry_price", bl.get("sector_benchmark"))):
            if pk in bl and not bl.get(pk) and tk:
                v = price_at(series_map.get(tk), ed)
                if v:
                    bl[pk] = v

    # 1. 账本重算（权威写回）
    corrections = 0
    for h in data["hypotheses"]:
        lo, p = recompute_ledger(h)
        cur = h.setdefault("current", {})
        if abs((cur.get("log_odds") or 0) - lo) > 1e-4 or abs((cur.get("p") or 0) - p) > 1e-4:
            corrections += 1
        cur.update({"log_odds": lo, "p": p, "recomputed_from_ledger": True})
        if args.append_history and h.get("status") == "observing":
            ph = h.setdefault("posterior_history", [])
            if ph and ph[-1].get("date") == TODAY:
                ph[-1] = {"date": TODAY, "p": p, "note": args.append_history}
            else:
                ph.append({"date": TODAY, "p": p, "note": args.append_history})
            cur["last_reviewed"] = TODAY

    # 4. 归档满 30 天的已关闭假设
    cutoff = (datetime.now() - timedelta(days=ARCHIVE_AFTER_DAYS)).strftime("%Y-%m-%d")
    keep, moved = [], []
    for h in data["hypotheses"]:
        resolved_at = (h.get("resolution") or {}).get("resolved_at")
        if h.get("status") != "observing" and resolved_at and resolved_at < cutoff:
            moved.append(h)
        else:
            keep.append(h)
    if moved:
        archive.setdefault("hypotheses", []).extend(moved)
        data["hypotheses"] = keep
        ARCHIVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ARCHIVE_PATH.write_text(json.dumps(archive, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    data["$last_updated"] = TODAY
    HYP_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 2. 复核输入表
    review_rows = []
    for h in data["hypotheses"]:
        if h.get("status") != "observing":
            continue
        rb = returns_block(h, series_map)
        lo, p = recompute_ledger(h)
        deadlines = [{
            "id": vc.get("id"), "metric": vc.get("metric"), "check_by": vc.get("check_by"),
            "days_left": (datetime.strptime(vc["check_by"], "%Y-%m-%d") - datetime.now()).days
                         if vc.get("check_by") else None,
            "status": vc.get("status"),
        } for vc in h.get("validation_conditions") or []]
        price_ev_this_week = sum(
            1 for ev in h.get("evidence_log") or []
            if ev.get("type") == "P_price" and not ev.get("pending")
            and ev.get("date", "") >= (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"))
        pending = [ev.get("id") for ev in h.get("evidence_log") or [] if ev.get("pending")]
        review_rows.append({
            "id": h["id"], "ticker": h.get("ticker"),
            "current_log_odds": lo, "current_p": p,
            "prior_p": (h.get("prior") or {}).get("p"),
            "returns": rb,
            "price_evidence_suggested_label": price_evidence_suggestion(rb["excess_vs_sector_pct"]
                                                                        if rb["excess_vs_sector_pct"] is not None
                                                                        else rb["excess_vs_spy_pct"]),
            "price_evidence_already_this_week": price_ev_this_week > 0,
            "pending_evidence_ids": pending,
            "validation_deadlines": deadlines,
            "review_deadline": (h.get("horizon") or {}).get("review_deadline"),
            "days_in_pool": (datetime.now() - datetime.strptime(h["created_at"], "%Y-%m-%d")).days
                            if h.get("created_at") else None,
        })
    REVIEW_INPUT_PATH.write_text(json.dumps(
        {"$generated_at": TODAY, "rows": review_rows}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    # 3. 评分卡
    scorecard = build_scorecard(data["hypotheses"], archive.get("hypotheses", []), series_map)
    SCORECARD_PATH.write_text(json.dumps(scorecard, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(f"✅ compute-metrics 完成：{len(data['hypotheses'])} 条在池（纠正 {corrections} 条后验）"
          f"，归档 {len(moved)} 条，复核输入 {len(review_rows)} 行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
