"""组织：满意度、工单积压、离职倾向。"""

from __future__ import annotations

from core.model.state import GameState


def tick(state: GameState, workload: float = 1.0, incidents: int = 0) -> None:
    for name, sat in list(state.org.satisfaction.items()):
        delta = 2.0 - 3.0 * max(0.0, workload - 0.8) - 0.8 * incidents
        state.org.satisfaction[name] = max(0.0, min(100.0, sat + delta))
    state.org.tickets = int(state.org.tickets * 0.95)


def quit_risk(state: GameState) -> float:
    """每周离职概率（用于事件判定）。"""
    if not state.org.satisfaction:
        return 0.0
    worst = min(state.org.satisfaction.values())
    return max(0.0, (55.0 - worst) / 100.0 * 0.06)
