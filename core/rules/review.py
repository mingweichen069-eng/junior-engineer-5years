"""阶段性述职（v0.9）：到玩家选定的周自动结算一次评价。

评价**只作参考**，不结束游戏：它把这局跑出来的数字整理成一组「数据成就」，
玩家看完可以点「继续游玩」，一直到角色真的失败（裁员 / 技术破产）为止。

设计取向：
- 不编数字。所有成就直接读 history / months / tech / debt / player 里的真实状态。
- 成就有门槛分档（金 / 银 / 铜 / 未达成），既表彰也指出短板。
- 「维护大屏参数」是独立成就：反复清理碎片、校准缓存、压测才算数，
  不是「开局加个索引躺到 60 周」能混过去的。
"""

from __future__ import annotations

from core.model.state import GameState
from core.rules import debt as debt_rules
from core.rules import difficulty as difficulty_rules
from core.rules import tech as tech_rules

RANK_ORDER = ["D", "C", "B", "A", "S"]
RANK_TEXT = {
    "S": "这段履历拿得出手。系统、账本、手艺，三样都没塌。",
    "A": "大局稳住了，还有一两处能再抠一抠。",
    "B": "中规中矩：没出大事，但也没把系统打磨到最好。",
    "C": "漏洞不少，只是还没在同一个月一起爆。",
    "D": "能撑到现在，主要靠运气和前人的索引。",
}


def _avg(xs: list[float], default: float = 0.0) -> float:
    return sum(xs) / len(xs) if xs else default


def _tier(pct: float) -> str:
    """达成度 → 金 / 银 / 铜 / 未达成。"""
    if pct >= 1.0:
        return "gold"
    if pct >= 0.72:
        return "silver"
    if pct >= 0.42:
        return "bronze"
    return "none"


def _achievement(aid, title, desc, value, target, unit, higher_better=True, fmt=None):
    if higher_better:
        pct = 0.0 if target <= 0 else max(0.0, min(1.0, value / target))
    else:
        # 越小越好：目标值本身算 100%，两倍目标算 0%
        pct = 1.0 if value <= target else max(0.0, min(1.0, 2 - value / max(1e-9, target)))
    display = fmt(value) if fmt else f"{value:g}{unit}"
    return {
        "id": aid,
        "title": title,
        "desc": desc,
        "value": round(float(value), 3),
        "target": target,
        "display": display,
        "unit": unit,
        "pct": round(pct * 100),
        "tier": _tier(pct),
        "earned": pct >= 1.0,
    }


def compute(state: GameState) -> dict:
    """生成一份完整的述职报告。纯读，不修改 state。"""
    hist = state.history or []
    recent12 = hist[-12:]
    months = state.months or []
    tech = tech_rules.load()
    total_nodes = len(tech["nodes"])
    unlocked = sum(1 for v in state.tech.unlocked.values() if v)

    avail = _avg([(1 - w.error_rate) * 100 for w in recent12], 100.0)
    p95 = _avg([w.p95_ms for w in recent12], 0.0)
    cash = state.company.cash_cents / 100
    profit = sum(m.profit_cents for m in months) / 100
    debt_pct = debt_rules.pressure(state) * 100
    calm_weeks = sum(1 for w in hist if w.event_count == 0)
    maint = sum(
        state.actions.counts.get(k, 0)
        for k in ("optimize_index", "audit_cache", "archive_orders", "rewrite_pending", "consolidate_servers")
    )
    passed = int(state.tech.exams_passed)
    failed = int(state.tech.exams_failed)

    achievements = [
        _achievement("uptime", "稳如老狗", "近 12 周平均可用性拉到 99.9%",
                     avail, 99.9, "%", fmt=lambda v: f"{v:.2f}%"),
        _achievement("latency", "大屏不卡", "近 12 周 p95 压到 200ms 以内",
                     p95, 200, "ms", higher_better=False, fmt=lambda v: f"{v:.0f}ms"),
        _achievement("growth", "用户还在涨", "注册用户做到 25 万",
                     state.company.registered, 250_000, "人", fmt=lambda v: f"{v:,.0f} 人"),
        _achievement("cash", "账上有粮", "现金储备 60 万",
                     cash, 600_000, "元", fmt=lambda v: f"¥{v:,.0f}"),
        _achievement("tech", "一直在学", f"掌握 12 个科技节点（共 {total_nodes} 个）",
                     unlocked, 12, "个", fmt=lambda v: f"{v:.0f} 个"),
        _achievement("exam", "真机过招", "通过 8 次真库 / 编码考核",
                     passed, 8, "次", fmt=lambda v: f"{v:.0f} 次"),
        _achievement("debt", "债没滚起来", "技术债压力压到 25% 以下",
                     debt_pct, 25, "%", higher_better=False, fmt=lambda v: f"{v:.0f}%"),
        _achievement("profit", "会算账", "累计利润 50 万",
                     profit, 500_000, "元", fmt=lambda v: f"¥{v:,.0f}"),
        _achievement("calm", "安静的一周", "累计 20 个零事故周",
                     calm_weeks, 20, "周", fmt=lambda v: f"{v:.0f} 周"),
        _achievement("maint", "参数没放养", "累计 10 次索引 / 缓存 / 容量维护",
                     maint, 10, "次", fmt=lambda v: f"{v:.0f} 次"),
        _achievement("endure", "还在工位", f"坚持到第 {state.meta.review_week} 周",
                     state.meta.week, max(1, state.meta.review_week), "周", fmt=lambda v: f"W{v:.0f}"),
    ]

    # 评级 = 达成度加权（科技 / 考核 / 维护略重，鼓励「持续经营」而不是「开局躺赢」）
    weights = {"tech": 1.5, "exam": 1.5, "maint": 1.5, "uptime": 1.3, "debt": 1.2}
    tw = sum(weights.get(a["id"], 1.0) for a in achievements)
    score = sum(a["pct"] * weights.get(a["id"], 1.0) for a in achievements) / tw
    rank = "S" if score >= 88 else "A" if score >= 72 else "B" if score >= 55 else "C" if score >= 38 else "D"

    earned = [a for a in achievements if a["earned"]]
    weak = sorted(achievements, key=lambda a: a["pct"])[:3]
    headline = (
        f"拿下 {len(earned)}/{len(achievements)} 项数据成就"
        + (f"，最亮眼的是「{earned[0]['title']}」" if earned else "，一项都还没拿到")
        + "。"
    )
    advice = "下一步可以补：" + "、".join(a["title"] for a in weak if not a["earned"]) if weak else ""

    cfg = difficulty_rules.get(state)
    return {
        "week": state.meta.week,
        "review_week": state.meta.review_week,
        "difficulty": state.meta.difficulty,
        "difficulty_label": cfg.get("label", state.meta.difficulty),
        "score": round(score, 1),
        "rank": rank,
        "rank_text": RANK_TEXT[rank],
        "headline": headline,
        "advice": advice,
        "kpis": [
            {"label": "坚持", "value": f"W{state.meta.week}", "note": f"目标 W{state.meta.review_week}"},
            {"label": "可用性", "value": f"{avail:.2f}%", "note": "近 12 周"},
            {"label": "大屏 p95", "value": f"{p95:.0f}ms", "note": "近 12 周"},
            {"label": "注册用户", "value": f"{state.company.registered:,}", "note": "人"},
            {"label": "掌握科技", "value": f"{unlocked}/{total_nodes}", "note": "节点"},
            {"label": "考核", "value": f"{passed} 过 / {failed} 挂", "note": "真机"},
        ],
        "achievements": achievements,
        "milestones": list(state.review.milestones),
        "timeline": list(state.ending.timeline[-10:]),
        "ended": state.ending.ended,
    }


def should_review(state: GameState) -> bool:
    """到点且还没生成过这一轮评价（沙盘不评价）。"""
    return (
        not state.meta.sandbox
        and not state.meta.review_done
        and state.meta.week >= int(state.meta.review_week)
        and not state.ending.ended
    )


def settle(state: GameState) -> dict:
    """到点结算：存一份报告、挂起面板，等玩家决定继续还是收手。"""
    report = compute(state)
    state.review.last = report
    state.review.reports.append(report)
    state.review.pending = True
    state.meta.review_done = True
    label = f"W{state.meta.week} 阶段性述职：{report['rank']} 级，{report['score']} 分"
    state.ending.timeline.append(label)
    return report
