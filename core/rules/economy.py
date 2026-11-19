"""公司经济：收入、成本、月结。"""

from __future__ import annotations

from core.model.state import GameState, MonthSnapshot
from core.rules import constants as C
from core.rules.traffic import traffic

# 每月固定成本（元）
DB_COST_YUAN = 2_800
REDIS_COST_YUAN = 600
BANDWIDTH_YUAN = 1_800
SALARIES_YUAN = 9 * 21_000
OFFICE_YUAN = 18_000
MISC_YUAN = 8_000
SERVER_COST_YUAN = 1_200
REPLICA_COST_YUAN = 1_500
SLA_PENALTY_YUAN = 30_000


def orders_per_day(state: GameState, reliability: float = 1.0) -> float:
    return traffic(state.company.registered).dau * C.ORDER_RATE_PER_DAU * reliability


def monthly_revenue_cents(state: GameState, reliability: float = 1.0) -> int:
    gmv = orders_per_day(state, reliability) * C.AOV_YUAN * C.DAYS_PER_MONTH
    return int(round(gmv * C.TAKE_RATE * 100))


def monthly_cost_cents(state: GameState) -> int:
    yuan = (
        state.app.servers * SERVER_COST_YUAN
        + state.app.read_replicas * REPLICA_COST_YUAN
        + DB_COST_YUAN
        + (REDIS_COST_YUAN if state.cache.enabled else 0)
        + BANDWIDTH_YUAN
        + SALARIES_YUAN
        + OFFICE_YUAN
        + MISC_YUAN
    )
    return yuan * 100 + state.company.marketing_cents


def weekly_revenue_cents(state: GameState, reliability: float = 1.0) -> int:
    """按周计提：月收入摊 1/4。"""
    return int(round(monthly_revenue_cents(state, reliability) / C.WEEKS_PER_MONTH))


def weekly_cost_cents(state: GameState) -> int:
    return int(round(monthly_cost_cents(state) / C.WEEKS_PER_MONTH))


SLA_TIERS = [
    # (下限可用性, 赔偿元, 信任变化, 工单增量)
    (0.995, 0, 0.0, 0),
    (0.990, 10_000, -3.0, 20),
    (0.980, 30_000, -8.0, 60),
    (0.000, 60_000, -15.0, 150),
]


def sla_outcome(availability: float) -> tuple[int, float, int]:
    """阶梯化 SLA：掉得越深，罚得越重，而不是一刀切。"""
    for floor, penalty, trust, tickets in SLA_TIERS:
        if availability >= floor:
            return penalty, trust, tickets
    return SLA_TIERS[-1][1], SLA_TIERS[-1][2], SLA_TIERS[-1][3]


def settle_month(state: GameState, availability: float, p95_ms: float) -> MonthSnapshot:
    """月结：汇总本月的 4 个周快照，按阶梯扣赔偿。不重复计入收入/成本。"""
    month_no = (state.meta.week - 2) // C.WEEKS_PER_MONTH + 1
    recent = state.history[-C.WEEKS_PER_MONTH:]
    revenue = sum(w.revenue_cents for w in recent)
    cost = sum(w.cost_cents for w in recent)

    penalty_yuan, trust_delta, tickets = sla_outcome(availability)
    penalty = penalty_yuan * 100
    if penalty:
        state.company.cash_cents -= penalty
    if trust_delta:
        state.org.manager_trust = max(0.0, min(100.0, state.org.manager_trust + trust_delta))
    if tickets:
        state.org.tickets += tickets

    snapshot = MonthSnapshot(
        month=month_no,
        revenue_cents=revenue,
        cost_cents=cost,
        profit_cents=revenue - cost - penalty,
        cash_cents=state.company.cash_cents,
        availability=round(availability, 4),
        p95_ms=round(p95_ms, 1),
        sla_penalty_cents=penalty,
    )
    state.months.append(snapshot)
    return snapshot
