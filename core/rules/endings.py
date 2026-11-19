"""结局与裁员状态机。

不是线性触发：由处境分（现金、可用性、绩效、信任、行业压力）累积，
经过 safe → watch → warn → list 四档，带迟滞与挽救窗口。
"""

from __future__ import annotations

from core.model.state import GameState
from core.rules import economy

GRADE_SCORE = {"S": 4.0, "A": 3.5, "B": 3.0, "C": 2.0, "D": 1.0}
STAGES = ["safe", "watch", "warn", "list"]
STAGE_TEXT = {
    "safe": "",
    "watch": "周明远最近找我聊了两次进度，语气比平时客气。我不太喜欢这种客气。",
    "warn": "我下班后开始刷招聘网站。不是因为想走，是因为不知道下个月还在不在。",
    "list": "HR 约我周四下午三点，会议室 B。我知道那个会议室是干什么用的。",
}
END_TEXT = {
    "funded": "王总在全员会上说，我们拿到了 A 轮。我盯着屏幕，想起三个月前那台快满的磁盘。",
    "unfunded": "系统没崩，用户还在，团队还在。只是投资人说时机不对。没人做错什么，但这一年没有下一轮了。",
    "layoff": "HR 说这不是针对我个人。我点头，收拾东西，把工牌放在桌上。",
    "bankrupt": "客户在群里问，数据还能恢复吗。没有人回答。",
}
MIN_DWELL = 2          # 每档至少停留两周
RESCUE_WEEKS = 4       # 预警后的挽救窗口


def runway_weeks(state: GameState) -> float:
    """按当前净现金流估算还能撑几周；盈利且现金为正则视为充足。"""
    if state.company.cash_cents <= 0:
        return 0.0
    revenue = economy.monthly_revenue_cents(state)
    cost = economy.monthly_cost_cents(state)
    profit = revenue - cost
    if profit >= 0:
        return 52.0
    return max(0.0, state.company.cash_cents / (-profit) * 4)


def availability_mtd(state: GameState) -> float:
    recent = state.history[-4:]
    if not recent:
        return 1.0
    return max(0.0, 1.0 - sum(w.error_rate for w in recent) / len(recent))


def perf_avg(state: GameState) -> float:
    months = state.months[-3:]
    if not months:
        return 3.0
    return sum(GRADE_SCORE.get(m.grade, 3.0) for m in months) / len(months)


def score(state: GameState) -> tuple[float, dict]:
    runway = runway_weeks(state)
    avail = availability_mtd(state)
    perf = perf_avg(state)
    trust = state.org.manager_trust
    wave = state.ending.wave_pressure
    parts = {
        "cash": -30 * min(1.0, max(0.0, (12 - runway) / 12)),
        "availability": -25 * min(1.0, max(0.0, (99.5 - avail * 100) / 1.5)),
        "performance": -20 * min(1.0, max(0.0, (3.5 - perf) / 2)),
        "trust": -15 * min(1.0, max(0.0, (60 - trust) / 60)),
        "wave": -10 * wave,
    }
    total = round(100 + sum(parts.values()), 1)
    # 硬保护：现金充足且稳定时不会被裁
    if runway > 26 and avail > 0.995:
        total = max(total, 45.0)
    return total, {k: round(v, 1) for k, v in parts.items()}


def _target_stage(total: float) -> str:
    if total >= 60:
        return "safe"
    if total >= 40:
        return "watch"
    if total >= 25:
        return "warn"
    return "list"


def _grade(state: GameState) -> str:
    from core.rules import career

    return career.grade(state, availability_mtd(state))


def _pick_rescue(state: GameState) -> tuple[str, float]:
    avail = availability_mtd(state)
    if avail < 0.995:
        return "stability", 0.995
    if runway_weeks(state) < 8:
        return "cash", 8.0
    return "growth", round(state.company.registered * 1.08)


def _rescue_done(state: GameState) -> bool:
    goal = state.ending.rescue_goal
    target = state.ending.rescue_target
    if not goal:
        return False
    if goal == "stability":
        return availability_mtd(state) >= target
    if goal == "cash":
        return runway_weeks(state) >= target
    if goal == "growth":
        return state.company.registered >= target
    return False


def tick(state: GameState) -> list[str]:
    """每周调用；返回本周发生的处境变化文案。"""
    ending = state.ending
    if ending.ended:
        return []

    month = (state.meta.week - 1) // 4 + 1
    # 长局压力：行业环境一年比一年紧，慢慢把处境往「名单」推。
    # 50~100 周不会变成永动机，但也不会在前期把人莫名其妙裁掉。
    ending.wave_pressure = min(0.95, max(0.0, (month - 4) * 0.045))
    if month >= 7:
        ending.wave_pressure = max(ending.wave_pressure, 0.2)
    if month >= 12:
        ending.wave_pressure = max(ending.wave_pressure, 0.45)
    if month >= 20:
        ending.wave_pressure = max(ending.wave_pressure, 0.7)

    total, parts = score(state)
    ending.last_score = total
    ending.last_breakdown = parts
    ending.weeks_in_stage += 1
    target = _target_stage(total)
    news: list[str] = []

    # 进入预警：给一个 4 周的挽救目标
    if target == "warn" and ending.stage != "warn" and ending.stage != "list":
        goal, value = _pick_rescue(state)
        ending.rescue_goal = goal
        ending.rescue_target = value
        ending.rescue_deadline = state.meta.week + RESCUE_WEEKS

    # 阶段迁移。注意：进入「名单」之后不再往回走——名单是流程终点，
    # 只能靠结局判定结束，否则「进入名单 → 再判 warn → 又给一个新窗口」会无限循环。
    if target != ending.stage and ending.weeks_in_stage >= MIN_DWELL and ending.stage != "list":
        cur = STAGES.index(ending.stage)
        nxt = STAGES.index(target)
        step = 1 if nxt > cur else -1
        ending.stage = STAGES[cur + step]
        ending.weeks_in_stage = 0
        if ending.stage == "warn":
            goal, value = _pick_rescue(state)
            ending.rescue_goal = goal
            ending.rescue_target = value
            ending.rescue_deadline = state.meta.week + RESCUE_WEEKS
        if ending.stage == "safe":
            ending.rescue_goal = None
        news.append(STAGE_TEXT.get(ending.stage, ""))
        ending.timeline.append(f"W{state.meta.week} 处境进入「{ending.stage}」（S={total}）")

    # 挽救窗口
    if ending.stage == "warn" and ending.rescue_goal:
        if _rescue_done(state):
            ending.stage = "watch"
            ending.weeks_in_stage = 0
            ending.rescue_goal = None
            state.org.manager_trust = min(100.0, state.org.manager_trust + 5)
            news.append("挽救目标达成，处境回到「观察」。")
            ending.timeline.append(f"W{state.meta.week} 挽救目标达成")
        elif state.meta.week >= ending.rescue_deadline:
            ending.stage = "list"
            ending.weeks_in_stage = 0
            ending.rescue_goal = None
            news.append(STAGE_TEXT["list"])
            ending.timeline.append(f"W{state.meta.week} 挽救窗口结束，进入「名单」")

    # 里程碑：A 轮资格与融资窗口只记进履历，**不再强行结束游戏**。
    # v0.9 起阶段性述职是唯一的「结算」，而且只是参考；真正让这一局停下来的
    # 只有角色失败（技术破产 / 被裁员）。玩家想继续经营就一直经营下去。
    if _funded(state) and not any("A 轮资格" in m for m in state.review.milestones):
        state.review.milestones.append(f"W{state.meta.week} 具备 A 轮资格")
        news.append("投资人那边有戏了：这一轮评审条件你够得着。做不做到底，你自己定。")
    if state.meta.week >= 24 and not any("融资窗口" in m for m in state.review.milestones):
        state.review.milestones.append("W24 融资窗口关闭，公司转为自负盈亏")
        news.append("这一轮融资窗口关上了。系统还转着，日子还得过。")

    # 结局判定：只保留「角色失败」。
    # 破产只有在「预警/名单」之前才会立刻结束；进入挽救窗口后玩家有完整 4 周去补救。
    if cash_bankrupt(state) and ending.stage in ("safe", "watch"):
        ended_kind = "bankrupt"
    elif ending.stage == "list" and ending.weeks_in_stage >= MIN_DWELL:
        ended_kind = "layoff"
    else:
        ended_kind = None

    if ended_kind:
        ending.ended = ended_kind
        ending.ended_week = state.meta.week
        label = {
            "bankrupt": "技术破产",
            "layoff": "被裁员",
            "funded": "拿到 A 轮",
            "unfunded": "融资失败，但系统健康",
        }[ended_kind]
        if ended_kind == "funded" and ending.flavor:
            label = f"拿到 A 轮 · {ending.flavor}"
        ending.timeline.append(f"W{state.meta.week} {label}")

    if ending.ended:
        news.append(END_TEXT[ending.ended])
    return news


def _bankrupt(state: GameState) -> bool:
    """技术破产：连续两个月的可用性都低于本难度的红线。"""
    from core.rules import difficulty as difficulty_rules

    redline = float(difficulty_rules.get(state)["availability_redline"])
    months = state.months[-2:]
    return len(months) == 2 and all(m.availability < redline for m in months)


def cash_bankrupt(state: GameState) -> bool:
    """公司现金流断裂：现金为负，且连续两个月基本没收入（可用性崩了）。"""
    return state.company.cash_cents < 0 and _bankrupt(state)


def _funded(state: GameState) -> bool:
    """MVP 版 A 轮门槛：第 24 周结算时按 6 个月规模缩放。

    门槛随难度变化（评审线、可用性线、融资窗口），
    并额外要求技术债压力降到 50% 以下——靠缓存糊指标的路线拿不到 A 轮。
    """
    if state.meta.week < 24:
        return False
    from core.rng import rng
    from core.rules import debt as debt_rules
    from core.rules import difficulty as difficulty_rules

    cfg = difficulty_rules.get(state)
    # 融资窗口是外部条件：不是"做对就一定赢"
    if rng(state.meta.seed, 24, "funding").random() > float(cfg["funding_window"]):
        return False

    last = state.months[-1] if state.months else None
    recent = state.months[-3:]
    avg_avail = sum(m.availability for m in recent) / len(recent) if recent else 0.0
    by_cat = debt_rules.breakdown(state)
    return (
        state.company.registered >= int(cfg["funding_registered"])
        and state.company.cash_cents > 0
        and debt_rules.pressure(state) < 0.55
        and max(by_cat.values()) < 0.75
        and last is not None
        and avg_avail >= float(cfg["funding_avail"])
    )
