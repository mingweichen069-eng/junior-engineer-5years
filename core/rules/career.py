"""职业线：月度绩效分与等级。"""

from __future__ import annotations

from core.model.state import GameState
from core.rules import debt as debt_rules


def skill_level(state: GameState) -> float:
    """工程经验折算成 0~1：只用于绩效里的「职业评价」一项。

    它不是科技树等级，也不解锁任何东西。同一份经验还被工作台用来降低
    调查消耗与变更事故风险（workbench._experience_factor）。
    """
    skills = state.player.skills or {}
    avg = sum(float(v) for v in skills.values()) / max(1, len(skills))
    return max(0.0, min(1.0, avg / 70.0))


def performance_score(state: GameState, availability: float) -> float:
    avail = max(0.0, min(1.0, availability / 0.995))
    trust = state.org.manager_trust / 100.0
    debt = 1.0 - debt_rules.pressure(state)
    skill = skill_level(state)
    # 工程经验只占 10% 权重进绩效：只会写文档、没真解决过问题的人拿不到 S
    return round(100.0 * (0.45 * avail + 0.25 * trust + 0.20 * debt + 0.10 * skill), 1)


def grade(state: GameState, availability: float) -> str:
    score = performance_score(state, availability)
    if score >= 85:
        return "S"
    if score >= 70:
        return "A"
    if score >= 55:
        return "B"
    if score >= 40:
        return "C"
    return "D"
