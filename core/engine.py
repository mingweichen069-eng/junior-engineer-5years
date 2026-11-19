"""游戏引擎入口：新游戏与周推进。"""

from __future__ import annotations

import json
from pathlib import Path

from core.model.state import (
    App, Cache, Column, Company, Db, Debt, DebtItem, GameState, Index, Meta,
    Org, Player, Table, TechState, WeekSnapshot,
)
from core.rng import rng
from core.rules import actions as action_rules
from core.rules import career, debt as debt_rules, economy, endings, events as event_rules, org as org_rules
from core.rules import tech as tech_rules
from core.rules import review as review_rules
from core.rules import difficulty as difficulty_rules
from core.rules import constants as C
from core.rules.evaluate import PeakMetrics, evaluate_peak

CONTENT = Path(__file__).resolve().parents[1] / "content"

DEBTS = [
    DebtItem("nonsargable_daily_report", 3.0, 0.02, category="query", name="大屏 DATE() 索引失效"),
    DebtItem("q1_wrong_index", 2.0, 0.01, category="query", name="订单查询用错索引"),
    DebtItem("no_monitoring", 4.0, 0.0, category="ops", name="没有监控"),
    DebtItem("no_backup", 5.0, 0.0, category="ops", name="没有备份"),
    DebtItem("no_circuit_breaker", 3.0, 0.015, category="arch", name="没有限流熔断"),
]


def _load_schema() -> dict:
    return json.loads((CONTENT / "schema.json").read_text(encoding="utf-8"))


def build_db(schema: dict | None = None) -> Db:
    schema = schema or _load_schema()
    tables: dict[str, Table] = {}
    for name, spec in schema["tables"].items():
        tables[name] = Table(
            name=name,
            rows=spec["rows"],
            row_bytes=spec["row_bytes"],
            fill_factor=spec["fill_factor"],
            columns={c: Column(**v) for c, v in spec["columns"].items()},
            indexes={k: Index(**v) for k, v in spec.get("indexes", {}).items()},
        )
    return Db(tables=tables)


def new_game(seed: int = 20261118, difficulty: str = "normal", sandbox: bool = False,
             review_week: int = 60) -> GameState:
    cfg = difficulty_rules.config(difficulty)
    # 每档难度有独立的种子偏移：同一个 seed 在不同难度下不是同一局
    eff_seed = seed + int(cfg["seed_offset"])
    tech = TechState()
    for node_id in cfg["unlock_tech"]:
        tech.unlocked[node_id] = True
    if tech.unlocked:
        tech.direction = "db"
    return GameState(
        meta=Meta(seed=eff_seed, week=1, difficulty=difficulty, sandbox=sandbox,
                  review_week=max(50, min(100, int(review_week)))),
        company=Company(cash_cents=int(cfg["cash_cents"]), registered=180_000),
        db=build_db(),
        cache=Cache(),
        app=App(),
        org=Org(),
        player=Player(ap=int(cfg.get("ap_base", 24)), skills=dict(cfg["start_skills"])),
        tech=tech,
        debt=Debt(items={
            d.id: DebtItem(d.id, d.severity, d.growth, d.resolved, d.category, d.name)
            for d in DEBTS
        }),
    )


def _grow_data(state: GameState) -> None:
    """订单表随业务增长。"""
    from core.rules.traffic import traffic

    t = traffic(state.company.registered)
    orders_day = t.dau * C.ORDER_RATE_PER_DAU
    new_orders = int(orders_day * C.DAYS_PER_WEEK)
    orders = state.db.tables["orders"]
    orders.rows += new_orders
    state.db.tables["order_items"].rows += new_orders * 3
    if orders.columns["id"].cardinality < orders.rows:
        orders.columns["id"].cardinality = orders.rows
        orders.columns["created_at"].cardinality = orders.rows


def _month_availability(state: GameState) -> tuple[float, float]:
    recent = state.history[-C.WEEKS_PER_MONTH:]
    if not recent:
        return 1.0, 0.0
    avg_error = sum(w.error_rate for w in recent) / len(recent)
    avg_p95 = sum(w.p95_ms for w in recent) / len(recent)
    return max(0.0, 1.0 - avg_error), avg_p95


def month_weeks(state: GameState) -> list[WeekSnapshot]:
    """本月的 4 个周快照（按周对齐，不依赖调用顺序）。"""
    end = state.meta.week - 1
    start = end - C.WEEKS_PER_MONTH + 1
    return [w for w in state.history if start <= w.week <= end]


def apply_growth(state: GameState, availability: float) -> float:
    """月末用户增长：流失取决于**本月**的错误率与延迟。"""
    weeks = month_weeks(state)
    if weeks:
        error = sum(w.error_rate for w in weeks) / len(weeks)
        p95 = sum(w.p95_ms for w in weeks) / len(weeks)
    else:
        error = 1.0 - availability
        p95 = 0.0
    latency_penalty = min(0.02, max(0.0, (p95 - 300) / 5000))
    churn = min(C.CHURN_MAX_MONTH, C.CHURN_BASE_MONTH + 1.5 * error + latency_penalty)
    month_no = (state.meta.week - 1) // C.WEEKS_PER_MONTH + 1
    jitter = rng(state.meta.seed, month_no, "growth").uniform(-0.006, 0.006)
    growth = C.G_ORGANIC_MONTH - churn + jitter
    state.company.registered = max(1000, int(state.company.registered * (1 + growth)))
    return growth


def _apply_traffic_spike(state: GameState) -> None:
    """流量毛刺：小活动随机 +1.8~3 倍；固定大促在第 12~13 周。

    大促可预见（第 10 周会收到通知），玩家可以提前扩容/限流/预热缓存。
    """
    week = state.meta.week
    mult = 1.0
    roll = rng(state.meta.seed, week, "traffic_spike").random()
    if roll < 0.06:
        mult = 1.6 + rng(state.meta.seed, week, "spike_size").random() * 0.8
    if 12 <= week <= 13:
        mult = max(mult, 2.2)
    state.flags["spike_mult"] = mult
    state.flags["promo_next_week"] = 12 if week == 10 else 0


def _bloat_tick(state: GameState, metrics: PeakMetrics) -> None:
    """写流量会让二级索引产生碎片，写入成本随之上升。删改越多涨得越快。"""
    write_qps = sum(m.qps for m in metrics.queries if m.path == "write")
    if write_qps <= 0:
        return
    growth = write_qps * C.DAYS_PER_WEEK * 0.0004
    for table in state.db.tables.values():
        if not table.indexes:
            continue
        for idx in table.indexes.values():
            idx.bloat = min(0.35, idx.bloat + growth)


def step(state: GameState) -> PeakMetrics:
    """推进一周：应用到期动作 → 世界变化 → 结算 → 周快照 → 月结。"""
    if state.ending.ended:
        return evaluate_peak(state)

    state.flags["writes_enabled"] = True       # M6：写查询也参与模拟（加索引会拖慢写入）
    state.meta.week += 1
    action_rules.apply_due(state)
    action_rules.reset_ap(state)
    if state.meta.sandbox:
        state.player.ap = 999                    # 沙盘：AP 无限，只做实验
    # 上周没做的决策，按默认选项结算；上周没交卷的考核也算一次未通过
    event_rules.expire_pending(state)
    tech_rules.expire_exams(state)
    # 自学精力回复 + 在读节点随时间推进（不花行动力；精力越高读得越快）。
    # 上周动作越多，这一周的精力回复被打折得越狠（救火挤掉学习）。
    tech_rules.tick_study(state)
    state.actions.actions_this_week = 0

    _grow_data(state)
    month_boundary = state.meta.week > 1 and (state.meta.week - 1) % C.WEEKS_PER_MONTH == 0
    _apply_traffic_spike(state)

    metrics = evaluate_peak(state)
    impact = event_rules.run_week(state, metrics)
    if impact.error_add > 0:
        metrics.error_rate = min(0.99, 1 - (1 - metrics.error_rate) * (1 - min(0.95, impact.error_add)))
    if impact.p95_add > 0:
        metrics.p95_ms = max(metrics.p95_ms, impact.p95_add)

    reliability = max(0.0, 1.0 - 2.0 * metrics.error_rate)
    revenue = economy.weekly_revenue_cents(state, reliability)
    cost = economy.weekly_cost_cents(state)
    state.company.cash_cents += revenue - cost

    debt_rules.tick(state, metrics.peak_qps / 78.0)
    org_rules.tick(state, workload=1.0, incidents=len(impact.fired))
    _bloat_tick(state, metrics)

    state.history.append(
        WeekSnapshot(
            week=state.meta.week,
            peak_qps=round(metrics.peak_qps, 2),
            p95_ms=round(metrics.p95_ms, 1),
            error_rate=round(metrics.error_rate, 4),
            disk_used_gb=round(metrics.disk_used_gb, 2),
            io_util=round(metrics.io_util, 4),
            cash_cents=state.company.cash_cents,
            revenue_cents=revenue,
            cost_cents=cost,
            event_count=len(impact.fired),
        )
    )

    # 大促考核记录：第 12~13 周可用性都 ≥98% 才算活下来
    if state.meta.week == 14:
        promo = [w for w in state.history if w.week in (12, 13)]
        if promo and all((1 - w.error_rate) >= 0.98 for w in promo):
            state.flags["survived_promo"] = True

    if month_boundary and not state.meta.sandbox:
        availability, avg_p95 = _month_availability(state)
        apply_growth(state, availability)
        snapshot = economy.settle_month(state, availability, avg_p95)
        snapshot.grade = career.grade(state, availability)

    if not state.meta.sandbox:
        endings.tick(state)
        # 到玩家选定的周：自动结算一次参考评价（挂起面板，但不结束这一局）
        if review_rules.should_review(state):
            review_rules.settle(state)
    return metrics
