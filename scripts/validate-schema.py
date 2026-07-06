#!/usr/bin/env python3
"""
validate-schema.py — 校验 data/hypotheses.json 的结构与数值纪律。

每次 LLM（skill）修改 hypotheses.json 后由 shell 脚本调用。
校验失败 exit 1，调用方应回滚（git checkout data/），绝不 push 脏数据。

校验内容：
1. JSON 结构：必填字段、类型、枚举值
2. 数值纪律：先验 ≤ 0.45、observing 后验 ∈ [0.05, 0.90]、LR 属于九档标尺、tier 与 LR 范围匹配
3. 账本一致性：current.log_odds ≈ prior.log_odds + Σ(非 pending 证据 log_lr)
4. 引用完整性：correlates_with / supersedes 指向存在的 id、日期格式
"""

import json
import math
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HYP_PATH = REPO_ROOT / "data" / "hypotheses.json"
ARCHIVE_PATH = REPO_ROOT / "data" / "archive" / "hypotheses-closed.json"

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ID_RE = re.compile(r"^H\d{8}-[A-Z0-9.\-]+$")

STATUSES = {"observing", "confirmed", "falsified", "expired", "promoted"}
EVIDENCE_TYPES = {"T1_hard_financial", "T2_supply_chain", "T3_soft_signal", "T4_narrative", "P_price"}
# 九档似然比标尺（label -> lr）
LR_SCALE = {
    "decisive_support": 5.0, "strong_support": 3.0, "moderate_support": 2.0,
    "weak_support": 1.5, "neutral": 1.0, "weak_against": 1 / 1.5,
    "moderate_against": 0.5, "strong_against": 1 / 3.0, "decisive_against": 0.2,
}
# tier -> 允许的 LR 范围（含端点）
TIER_LR_RANGE = {
    "T1_hard_financial": (0.2, 5.0),
    "T2_supply_chain": (0.4, 2.5),
    "T3_soft_signal": (1 / 1.5, 1.5),
    "T4_narrative": (1.0, 1.0),
    "P_price": (1 / 1.5, 1.5),
}
PRIOR_CAP = 0.45
POSTERIOR_FLOOR, POSTERIOR_CEIL = 0.05, 0.90
PRICE_LOGLR_CAP = 1.10  # 全生命周期价格类 |Σ log_lr| 封顶
LOG_ODDS_TOL = 0.02     # 账本重算容差

errors: list[str] = []
warnings: list[str] = []


def err(hid: str, msg: str) -> None:
    errors.append(f"[{hid}] {msg}")


def warn(hid: str, msg: str) -> None:
    warnings.append(f"[{hid}] {msg}")


def check_date(hid: str, field: str, val) -> None:
    if val is not None and (not isinstance(val, str) or not DATE_RE.match(val)):
        err(hid, f"{field} 不是 YYYY-MM-DD 格式: {val!r}")


def log_odds(p: float) -> float:
    return math.log(p / (1 - p))


def check_hypothesis(h: dict, all_ids: set[str]) -> None:
    hid = h.get("id", "<missing-id>")

    if not isinstance(h.get("id"), str) or not ID_RE.match(h["id"]):
        err(hid, f"id 格式应为 HYYYYMMDD-TICKER: {h.get('id')!r}")
    for f in ("ticker", "company", "created_at", "status"):
        if not h.get(f):
            err(hid, f"缺少必填字段 {f}")
    check_date(hid, "created_at", h.get("created_at"))

    if h.get("status") not in STATUSES:
        err(hid, f"status 非法: {h.get('status')!r}")

    # thesis
    thesis = h.get("thesis") or {}
    for f in ("one_liner", "demand_change", "transmission", "misclassification"):
        if not thesis.get(f):
            err(hid, f"thesis.{f} 缺失")
    score = thesis.get("serenity_score") or {}
    dims = ("demand_certainty", "transmission_clarity", "purity", "elasticity",
            "neglect", "verification_speed", "downside")
    for d in dims:
        v = score.get(d)
        if not isinstance(v, int) or not 1 <= v <= 5:
            err(hid, f"serenity_score.{d} 应为 1-5 整数: {v!r}")

    # prior
    prior = h.get("prior") or {}
    p0 = prior.get("p")
    if not isinstance(p0, (int, float)) or not 0 < p0 < 1:
        err(hid, f"prior.p 非法: {p0!r}")
    else:
        if p0 > PRIOR_CAP + 1e-9:
            err(hid, f"prior.p={p0} 超过硬上限 {PRIOR_CAP}")
        lo = prior.get("log_odds")
        if not isinstance(lo, (int, float)) or abs(lo - log_odds(p0)) > LOG_ODDS_TOL:
            err(hid, f"prior.log_odds={lo!r} 与 prior.p={p0} 不一致（应为 {log_odds(p0):.4f}）")
    if not prior.get("rationale"):
        err(hid, "prior.rationale 缺失")

    # validation_conditions
    vcs = h.get("validation_conditions") or []
    if not vcs:
        err(hid, "validation_conditions 为空 — 假设不可检验")
    if not any(vc.get("required") for vc in vcs):
        err(hid, "没有任何 required=true 的验证条件")
    for vc in vcs:
        for f in ("id", "metric", "confirm_if", "falsify_if", "check_by"):
            if not vc.get(f):
                err(hid, f"验证条件 {vc.get('id', '?')} 缺少 {f}")
        check_date(hid, f"validation_conditions[{vc.get('id')}].check_by", vc.get("check_by"))
        if vc.get("status") not in {"pending", "met", "missed", "unclear"}:
            err(hid, f"验证条件 {vc.get('id')} status 非法: {vc.get('status')!r}")

    # horizon & baseline
    hz = h.get("horizon") or {}
    if not isinstance(hz.get("quarters"), int) or not 1 <= hz["quarters"] <= 4:
        err(hid, f"horizon.quarters 应为 1-4: {hz.get('quarters')!r}")
    check_date(hid, "horizon.review_deadline", hz.get("review_deadline"))
    bl = h.get("baseline") or {}
    for f in ("entry_date", "benchmark"):
        if bl.get(f) in (None, ""):
            err(hid, f"baseline.{f} 缺失")
    if not bl.get("entry_price"):
        err(hid, "baseline.entry_price 缺失或为 0")
    for f in ("benchmark_entry_price", "sector_benchmark_entry_price"):
        if bl.get(f) is not None and not bl.get(f):
            warn(hid, f"baseline.{f} 为 0 — compute-metrics.py 会从 prices.json 自动回填")
    check_date(hid, "baseline.entry_date", bl.get("entry_date"))

    # price_expectation（observing 状态必填）
    if h.get("status") == "observing":
        px = h.get("price_expectation") or {}
        ct, ft = px.get("confirm_target"), px.get("falsify_target")
        if not isinstance(ct, (int, float)) or ct <= 0:
            err(hid, f"price_expectation.confirm_target 缺失或非法: {ct!r}")
        if not isinstance(ft, (int, float)) or ft <= 0:
            err(hid, f"price_expectation.falsify_target 缺失或非法: {ft!r}")
        if isinstance(ct, (int, float)) and isinstance(ft, (int, float)) and ct <= ft:
            err(hid, f"confirm_target({ct}) 应大于 falsify_target({ft})")
        check_date(hid, "price_expectation.expected_by", px.get("expected_by"))
        if not px.get("expected_by"):
            err(hid, "price_expectation.expected_by 缺失")
        for f in ("methodology_cn", "timeline_logic_cn"):
            if not px.get(f) or len(str(px.get(f))) < 30:
                err(hid, f"price_expectation.{f} 缺失或过于敷衍（需写明可复算的数字逻辑）")

    # evidence_log
    price_loglr_sum = 0.0
    ev_ids = set()
    settled_loglr_sum = 0.0
    for ev in h.get("evidence_log") or []:
        eid = ev.get("id", "?")
        if eid in ev_ids:
            err(hid, f"证据 id 重复: {eid}")
        ev_ids.add(eid)
        check_date(hid, f"evidence[{eid}].date", ev.get("date"))
        etype = ev.get("type")
        if etype not in EVIDENCE_TYPES:
            err(hid, f"证据 {eid} type 非法: {etype!r}")
        if not ev.get("summary"):
            err(hid, f"证据 {eid} 缺少 summary")
        cw = ev.get("correlates_with")
        if cw is not None and cw not in ev_ids and cw != eid:
            # correlates_with 只能指向同一假设内更早的证据
            if cw not in {e.get("id") for e in h.get("evidence_log") or []}:
                err(hid, f"证据 {eid} correlates_with 指向不存在的证据: {cw!r}")

        lk = ev.get("likelihood") or {}
        label, lr, llr = lk.get("label"), lk.get("lr"), lk.get("log_lr")
        if ev.get("pending"):
            # pending 证据：likelihood 未定或 neutral 占位，不参与账本
            continue
        if label not in LR_SCALE:
            err(hid, f"证据 {eid} likelihood.label 不在九档标尺: {label!r}")
            continue
        expect_lr = LR_SCALE[label]
        if not isinstance(lr, (int, float)) or abs(lr - expect_lr) > 0.01:
            err(hid, f"证据 {eid} lr={lr!r} 与标签 {label}（应为 {expect_lr:.2f}）不符")
        if not isinstance(llr, (int, float)) or abs(llr - math.log(expect_lr)) > 0.01:
            err(hid, f"证据 {eid} log_lr={llr!r} 与标签 {label}（应为 {math.log(expect_lr):.4f}）不符")
        lo_range = TIER_LR_RANGE.get(etype)
        if lo_range and not (lo_range[0] - 1e-9 <= expect_lr <= lo_range[1] + 1e-9):
            err(hid, f"证据 {eid} tier {etype} 不允许 LR={expect_lr:.2f}（范围 {lo_range[0]:.2f}-{lo_range[1]:.2f}）")
        if cw is not None and abs(expect_lr - 1.0) > 1e-9:
            err(hid, f"证据 {eid} 标记 correlates_with 却给了非 neutral 的 LR（同源证据只记录不更新）")
        if etype == "P_price":
            price_loglr_sum += math.log(expect_lr)
        settled_loglr_sum += math.log(expect_lr)

    if abs(price_loglr_sum) > PRICE_LOGLR_CAP + 1e-9:
        err(hid, f"价格类证据 |Σlog_lr|={abs(price_loglr_sum):.3f} 超过生命周期封顶 {PRICE_LOGLR_CAP}")

    # current 与账本一致性
    cur = h.get("current") or {}
    p_cur = cur.get("p")
    lo_cur = cur.get("log_odds")
    if not isinstance(p_cur, (int, float)) or not 0 < p_cur < 1:
        err(hid, f"current.p 非法: {p_cur!r}")
    elif isinstance(prior.get("log_odds"), (int, float)):
        expect_lo = prior["log_odds"] + settled_loglr_sum
        if not isinstance(lo_cur, (int, float)) or abs(lo_cur - expect_lo) > LOG_ODDS_TOL:
            err(hid, f"账本不一致: current.log_odds={lo_cur!r}，应为 prior({prior['log_odds']:.4f}) + Σlog_lr({settled_loglr_sum:.4f}) = {expect_lo:.4f}")
        expect_p = 1 / (1 + math.exp(-expect_lo))
        if abs(p_cur - expect_p) > 0.01:
            err(hid, f"current.p={p_cur} 与账本重算值 {expect_p:.4f} 不符")
        if h.get("status") == "observing" and not (POSTERIOR_FLOOR - 1e-9 <= p_cur <= POSTERIOR_CEIL + 1e-9):
            err(hid, f"observing 状态后验 {p_cur} 超出夹逼区间 [{POSTERIOR_FLOOR}, {POSTERIOR_CEIL}]")

    # posterior_history
    ph = h.get("posterior_history") or []
    if not ph:
        err(hid, "posterior_history 为空（至少应有 prior 起点）")
    for pt in ph:
        check_date(hid, "posterior_history.date", pt.get("date"))
        if not isinstance(pt.get("p"), (int, float)):
            err(hid, f"posterior_history 有非数值 p: {pt!r}")

    # status_history
    sh = h.get("status_history") or []
    if not sh:
        err(hid, "status_history 为空")
    for st in sh:
        if st.get("to") not in STATUSES:
            err(hid, f"status_history 非法迁移目标: {st.get('to')!r}")
        if not st.get("reason"):
            err(hid, f"status_history 迁移缺少 reason")

    # links
    links = h.get("links") or {}
    sup = links.get("supersedes")
    if sup is not None and sup not in all_ids:
        warn(hid, f"links.supersedes 指向未知 id: {sup}（可能在归档中）")

    # 已关闭假设必须有 resolution.outcome
    if h.get("status") in {"confirmed", "falsified", "expired"}:
        res = h.get("resolution") or {}
        if res.get("outcome") not in {"hit", "miss"}:
            err(hid, f"状态 {h['status']} 但 resolution.outcome 未判定: {res.get('outcome')!r}")
        check_date(hid, "resolution.resolved_at", res.get("resolved_at"))


def main() -> int:
    if not HYP_PATH.exists():
        print(f"❌ 找不到 {HYP_PATH}", file=sys.stderr)
        return 1
    try:
        data = json.loads(HYP_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"❌ hypotheses.json 不是合法 JSON: {e}", file=sys.stderr)
        return 1

    hyps = data.get("hypotheses")
    if not isinstance(hyps, list):
        print("❌ 顶层缺少 hypotheses 数组", file=sys.stderr)
        return 1
    if not DATE_RE.match(data.get("$last_updated", "")):
        errors.append("顶层 $last_updated 不是 YYYY-MM-DD")

    all_ids = {h.get("id") for h in hyps}
    if ARCHIVE_PATH.exists():
        try:
            archived = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
            all_ids |= {h.get("id") for h in archived.get("hypotheses", [])}
        except json.JSONDecodeError:
            warnings.append("archive/hypotheses-closed.json 不是合法 JSON")

    ids_seen: set[str] = set()
    for h in hyps:
        hid = h.get("id", "?")
        if hid in ids_seen:
            errors.append(f"[{hid}] 假设 id 重复")
        ids_seen.add(hid)
        check_hypothesis(h, all_ids)

    # 同 ticker 只允许一条 observing
    observing_tickers: dict[str, str] = {}
    for h in hyps:
        if h.get("status") == "observing":
            t = h.get("ticker")
            if t in observing_tickers:
                errors.append(f"[{h.get('id')}] ticker {t} 已有活跃假设 {observing_tickers[t]} — 违反去重规则")
            else:
                observing_tickers[t] = h.get("id")

    for w in warnings:
        print(f"⚠️  {w}")
    if errors:
        print(f"\n❌ 校验失败，共 {len(errors)} 个错误：", file=sys.stderr)
        for e in errors:
            print(f"   · {e}", file=sys.stderr)
        return 1
    print(f"✅ hypotheses.json 校验通过（{len(hyps)} 条假设，{len(observing_tickers)} 条活跃）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
