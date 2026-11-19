"""职业线：月度绩效分与等级。"""

from __future__ import annotations

from core.model.state import GameState
from core.rules import debt as debt_rules


def skill_level(state: GameState) -> float:
    """技术能力：科技树与动手动作养出来的熟练度，折算成 0~1。"""
    skills = state.player.skills or {}
    avg = sum(float(v) for v in skills.values()) / max(1, len(skills))
    return max(0.0, min(1.0, avg / 70.0))


def performance_score(state: GameState, availability: float) -> float:
    avail = max(0.0, min(1.0, availability / 0.995))
    trust = state.org.manager_trust / 100.0
    debt = 1.0 - debt_rules.pressure(state)
    skill = skill_level(state)
    # 技术能力直接进绩效：只会写文档不会解决问题的人拿不到 S
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
