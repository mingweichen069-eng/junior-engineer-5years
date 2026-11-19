"""技术债：按查询 / 运维 / 架构三类分开计息，各自有不同的爆雷方式。"""

from __future__ import annotations

from core.model.state import GameState

CATEGORIES = ("query", "ops", "arch")
CATEGORY_LABEL = {"query": "查询债", "ops": "运维债", "arch": "架构债"}
MAX_ITEMS = 9


def spawn(
    state: GameState,
    debt_id: str,
    category: str,
    severity: float,
    growth: float = 0.02,
    name: str = "",
) -> bool:
    """新债随操作产生（设计文档 §12：债务计息、按负载加速、新债随操作产生）。

    已有的债会升级，而不是重复添加。
    """
    if debt_id in state.debt.items:
        item = state.debt.items[debt_id]
        if item.resolved:
            item.resolved = False
            item.severity = max(item.severity, severity)
        else:
            item.severity = min(5.0, max(item.severity, severity))
        return True
    if len(state.debt.items) >= MAX_ITEMS:
        return False
    from core.model.state import DebtItem

    state.debt.items[debt_id] = DebtItem(
        id=debt_id, severity=severity, growth=growth, category=category, name=name
    )
    return True


def unresolved_count(state: GameState) -> int:
    return sum(1 for i in state.debt.items.values() if not i.resolved)


def tick(state: GameState, load_factor: float) -> None:
    for item in state.debt.items.values():
        if item.resolved:
            continue
        # 架构债在高负载下涨得更快（缺熔断、缺限流）
        growth = item.growth * (1.8 if item.category == "arch" else 1.0)
        item.severity = min(5.0, item.severity * (1 + growth * max(0.0, load_factor)))


def pressure(state: GameState) -> float:
    """0~1 的总体债务压力。已解决的项计入分母但不计分子。"""
    items = list(state.debt.items.values())
    if not items:
        return 0.0
    active = sum(i.severity for i in items if not i.resolved)
    return min(1.0, active / (5.0 * len(items)))


def pressure_by(state: GameState, category: str) -> float:
    items = [i for i in state.debt.items.values() if i.category == category]
    if not items:
        return 0.0
    active = sum(i.severity for i in items if not i.resolved)
    return min(1.0, active / (5.0 * len(items)))


def breakdown(state: GameState) -> dict[str, float]:
    return {c: round(pressure_by(state, c), 3) for c in CATEGORIES}


def resolve(state: GameState, debt_id: str) -> None:
    if debt_id in state.debt.items:
        state.debt.items[debt_id].resolved = True
