"""事件总线：环境事件 + 决策事件。

两类事件：
- 环境事件（root=true，无 choices）：世界自己发生，立即结算，玩家只能事后看。
- 决策事件（decision=true，带 choices）：世界把选择权交给你，进入 pending 队列，
  前端弹窗；不选就在推进到下一周时按默认选项结算。

这样既保留「世界不会等你」的压力，又让关键节点真的有分叉。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from core.model.state import EventLogEntry, GameState, PendingEvent
from core.rng import rng
from core.rules.evaluate import PeakMetrics

CONTENT = Path(__file__).resolve().parents[2] / "content"
MAX_DEPTH = 3
MAX_EVENTS_PER_WEEK = 6
MAX_PENDING = 3          # 同时最多积压 3 个决策，多了就按默认结算


@dataclass
class Impact:
    error_add: float = 0.0
    p95_add: float = 0.0
    tickets: int = 0
    trust: float = 0.0
    cash_cents: int = 0
    fired: list[str] = field(default_factory=list)
    opened: list[str] = field(default_factory=list)   # 新开的决策事件 log_id


def load_events(path: Path | None = None) -> list[dict]:
    return json.loads((path or CONTENT / "events.json").read_text(encoding="utf-8"))["events"]


def build_context(state: GameState, metrics: PeakMetrics) -> dict:
    dash = metrics.by_id("q_dashboard")
    months = state.months[-1] if state.months else None
    return {
        "week": state.meta.week,
        "peak_qps": metrics.peak_qps,
        "p95_ms": metrics.p95_ms,
        "error_rate": metrics.error_rate,
        "io_util": metrics.io_util,
        "disk_pct": metrics.disk_used_gb / state.db.disk_capacity_gb,
        "q_dashboard_ms": dash.time_ms if dash else 0.0,
        "satisfaction_min": min(state.org.satisfaction.values()) if state.org.satisfaction else 100.0,
        "trust": state.org.manager_trust,
        "cash": state.company.cash_cents / 100,
        "dashboard_cached": 1 if "q_dashboard" in state.cache.policies else 0,
        "monitoring": 1 if state.actions.monitoring else 0,
        "tickets": state.org.tickets,
        "debt_pressure": 100.0 * _debt_pressure(state),
        "grade": {"S": 4, "A": 3, "B": 2, "C": 1, "D": 0}.get(months.grade, 2) if months else 2,
        "replicas": state.app.read_replicas,
        "pool_rho": metrics.pool_rho,
    }


def _debt_pressure(state: GameState) -> float:
    from core.rules import debt as debt_rules

    return debt_rules.pressure(state)


def _match(cond: dict, ctx: dict) -> bool:
    if not cond:
        return True
    if "all" in cond:
        return all(_match(c, ctx) for c in cond["all"])
    if "any" in cond:
        return any(_match(c, ctx) for c in cond["any"])
    value = ctx.get(cond["metric"], 0)
    op, target = cond["op"], cond["value"]
    if op == "gte":
        return value >= target
    if op == "lte":
        return value <= target
    if op == "gt":
        return value > target
    if op == "lt":
        return value < target
    if op == "eq":
        return value == target
    raise ValueError(f"未知比较符 {op}")


def _eligible(state: GameState, event: dict) -> bool:
    until = state.events.cooldowns.get(event["id"], 0)
    if state.meta.week < until:
        return False
    limit = event.get("max_per_game")
    if limit is not None and state.events.counts.get(event["id"], 0) >= limit:
        return False
    return True


def _apply_list(state: GameState, effects: list[dict], day: int, impact: Impact | None = None) -> dict:
    """把一组 effects 应用到 state。day 用于按周内剩余天数折算持续性影响。"""
    weight = (7 - day + 1) / 7.0
    applied: dict = {}
    for eff in effects:
        kind = eff["type"]
        value = eff.get("value", 0)
        if kind == "trust":
            state.org.manager_trust = max(0.0, min(100.0, state.org.manager_trust + value))
            applied["trust"] = value
        elif kind == "tickets":
            state.org.tickets = max(0, state.org.tickets + int(value))
            applied["tickets"] = int(value)
        elif kind == "cash":
            state.company.cash_cents += int(value)
            applied["cash_cents"] = int(value)
            if impact is not None:
                impact.cash_cents += int(value)
        elif kind == "error_add":
            # 开了监控/告警，事故的扩散会被压住：这是监控最直接的收益
            damp = 0.7 if state.actions.monitoring else 1.0
            if impact is not None:
                impact.error_add += float(value) * weight * damp
            applied["error_add"] = round(float(value) * weight * damp, 4)
        elif kind == "p95_add":
            if impact is not None:
                impact.p95_add = max(impact.p95_add, float(value))
            applied["p95_add"] = float(value)
        elif kind == "satisfaction":
            for name in state.org.satisfaction:
                state.org.satisfaction[name] = max(
                    0.0, min(100.0, state.org.satisfaction[name] + float(value))
                )
            applied["satisfaction"] = float(value)
        elif kind == "spawn_debt":
            from core.rules import debt as debt_rules

            debt_rules.spawn(
                state,
                eff.get("debt_id", "event_debt"),
                eff.get("category", "query"),
                float(eff.get("severity", 2.0)),
                float(eff.get("growth", 0.02)),
                eff.get("name", ""),
            )
            applied["debt"] = eff.get("debt_id", "")
        elif kind == "resolve_debt":
            from core.rules import debt as debt_rules

            debt_rules.resolve(state, eff.get("debt_id", ""))
            applied["resolved"] = eff.get("debt_id", "")
        elif kind == "flag":
            state.flags[str(eff.get("flag", ""))] = True
            applied["flag"] = eff.get("flag", "")
        elif kind == "skill":
            for name, amount in (eff.get("skills") or {}).items():
                state.player.skills[name] = min(100.0, state.player.skills.get(name, 0.0) + float(amount))
            applied["skill"] = eff.get("skills", {})
        elif kind == "ap":
            state.player.ap = max(0, state.player.ap + int(value))
            applied["ap"] = int(value)
        elif kind == "ap_bonus":
            # 永久提升每周行动力：招到人、把重复劳动自动化、拿到预算
            state.player.ap_bonus_event += int(value)
            state.player.ap = max(0, state.player.ap + int(value))
            applied["ap_bonus"] = int(value)
        elif kind == "ap_temp":
            # 临时加成：只持续 weeks 周（比如临时借调一个人）
            weeks = max(1, int(eff.get("weeks", 2)))
            state.player.ap_temp_bonus = int(value)
            state.player.ap_temp_until = state.meta.week + weeks
            state.player.ap = max(0, state.player.ap + int(value))
            applied["ap_temp"] = f"{int(value):+d}/{weeks}周"
        elif kind == "servers":
            state.app.servers = max(1, state.app.servers + int(value))
            applied["servers"] = int(value)
        elif kind == "replicas":
            state.app.read_replicas = max(0, state.app.read_replicas + int(value))
            applied["replicas"] = int(value)
        elif kind == "docs":
            state.actions.docs = max(0, state.actions.docs + int(value))
            applied["docs"] = int(value)
        else:  # pragma: no cover
            raise ValueError(f"未知事件效果 {kind}")
    return applied


def _apply(state: GameState, event: dict, day: int, impact: Impact) -> dict:
    return _apply_list(state, event.get("effects", []), day, impact)


def _fire_chain(
    state: GameState, root: dict, day: int, by_id: dict, impact: Impact
) -> None:
    seq = 0
    queue: list[dict] = [{"event": root, "day": day, "depth": 0, "cause": None}]
    fired = 0
    while queue and fired < MAX_EVENTS_PER_WEEK:
        queue.sort(key=lambda item: (item["day"], item["event"]["priority"], item["depth"]))
        node = queue.pop(0)
        event = node["event"]
        if not _eligible(state, event):
            continue
        log_id = f"W{state.meta.week}D{node['day']}#{seq}"
        seq += 1
        applied = _apply(state, event, node["day"], impact)
        state.events.log.append(
            EventLogEntry(
                log_id=log_id,
                event_id=event["id"],
                title=event["name"],
                week=state.meta.week,
                day=node["day"],
                cause_id=node["cause"],
                depth=node["depth"],
                effects=applied,
                tag=event.get("tag", "fault"),
                narrative=event.get("narrative", ""),
                speaker=event.get("speaker", ""),
            )
        )
        state.events.counts[event["id"]] = state.events.counts.get(event["id"], 0) + 1
        state.events.cooldowns[event["id"]] = state.meta.week + int(event.get("cooldown_weeks", 0))
        impact.fired.append(event["id"])
        fired += 1

        if node["depth"] < MAX_DEPTH:
            for child in event.get("emit", []):
                target = by_id.get(child["id"])
                if target is None:
                    continue
                queue.append({
                    "event": target,
                    "day": min(7, node["day"] + int(child.get("delay_days", 0))),
                    "depth": node["depth"] + 1,
                    "cause": log_id,
                })


def _open_decision(state: GameState, event: dict, day: int) -> str:
    log_id = f"W{state.meta.week}D{day}#D{len(state.events.pending)}"
    state.events.log.append(
        EventLogEntry(
            log_id=log_id,
            event_id=event["id"],
            title=event["name"],
            week=state.meta.week,
            day=day,
            effects={},
            tag=event.get("tag", "team"),
            narrative=event.get("narrative", ""),
            speaker=event.get("speaker", ""),
            pending=True,
        )
    )
    state.events.pending.append(
        PendingEvent(
            id=log_id,
            event_id=event["id"],
            week=state.meta.week,
            day=day,
            log_id=log_id,
            default_choice=event.get("default", ""),
        )
    )
    state.events.counts[event["id"]] = state.events.counts.get(event["id"], 0) + 1
    state.events.cooldowns[event["id"]] = state.meta.week + int(event.get("cooldown_weeks", 0))
    return log_id


def _decision_candidates(state: GameState, catalog: list[dict], ctx: dict, density: float) -> list[dict]:
    out = []
    for event in catalog:
        if not event.get("decision"):
            continue
        if not _eligible(state, event):
            continue
        if not _match(event.get("when", {}), ctx):
            continue
        chance = float(event.get("chance", 0.35))
        roll = rng(state.meta.seed, state.meta.week, f"dgate:{event['id']}").random()
        if roll > min(1.0, chance * max(0.6, density)):
            continue
        out.append(event)
    return out


def _maybe_decision(state: GameState, catalog: list[dict], ctx: dict, impact: Impact,
                    density: float, allow_new: bool = True) -> None:
    """决策事件独立于环境事件抽取：一周最多开 1 个，积压满了就先结算最旧的。

    积压清理**每周都要做**（否则事件间隔会把队列堵死）；只有「抽新决策」受间隔限制。
    """
    if state.meta.sandbox:
        return
    while len(state.events.pending) >= MAX_PENDING:
        oldest = min(state.events.pending, key=lambda p: (p.week, p.day, p.id))
        resolve_choice(state, oldest.id, "")
    if not allow_new:
        return
    cands = _decision_candidates(state, catalog, ctx, density)
    if not cands:
        return
    cands.sort(key=lambda e: e["id"])
    pick = rng(state.meta.seed, state.meta.week, "decision").choice(cands)
    day = rng(state.meta.seed, state.meta.week, "decision_day").randint(1, 7)
    log_id = _open_decision(state, pick, day)
    impact.opened.append(log_id)


def run_week(state: GameState, metrics: PeakMetrics, catalog: list[dict] | None = None) -> Impact:
    """抽取环境事件并解析因果链；再单独抽一个决策事件。"""
    catalog = catalog or load_events()
    by_id = {e["id"]: e for e in catalog}
    ctx = build_context(state, metrics)
    impact = Impact()

    from core.rules import difficulty as difficulty_rules

    cfg = difficulty_rules.get(state)
    density = float(cfg["event_density"])
    gap = int(cfg.get("event_gap_weeks", 3))
    # 两次事件之间至少隔 gap 周。关键剧情（essential，例如大促预告）不受间隔限制，
    # 但普通事故与决策事件都要等冷却，避免「点一周就弹一个」。
    gap_ok = state.meta.week - int(state.events.last_event_week) >= gap

    roots = [
        e for e in catalog
        if e.get("root") and not e.get("decision") and _eligible(state, e) and _match(e.get("when", {}), ctx)
    ]
    if not gap_ok:
        roots = [e for e in roots if e.get("essential")]
    roots = [
        e for e in roots
        if e.get("essential")
        or rng(state.meta.seed, state.meta.week, f"gate:{e['id']}").random() <= min(1.0, density)
    ]
    roots = [
        e for e in roots
        if e.get("chance") is None
        or rng(state.meta.seed, state.meta.week, f"chance:{e['id']}").random()
        <= min(1.0, float(e["chance"]) * density)
    ]
    if roots:
        roots.sort(key=lambda e: e["id"])
        root = rng(state.meta.seed, state.meta.week, "events").choice(roots)
        day = rng(state.meta.seed, state.meta.week, "event_day").randint(1, 7)
        _fire_chain(state, root, day, by_id, impact)

    _maybe_decision(state, catalog, ctx, impact, density, allow_new=gap_ok)
    if impact.fired or impact.opened:
        state.events.last_event_week = state.meta.week
    return impact


def _find_event(event_id: str, catalog: list[dict] | None = None) -> dict | None:
    return next((e for e in (catalog or load_events()) if e["id"] == event_id), None)


def _log_entry(state: GameState, log_id: str) -> EventLogEntry | None:
    return next((e for e in state.events.log if e.log_id == log_id), None)


def resolve_choice(state: GameState, pending_id: str, choice_id: str = "") -> tuple[bool, str]:
    """按玩家的选择结算；choice_id 为空时用默认选项（超时）。"""
    pending = next((p for p in state.events.pending if p.id == pending_id), None)
    if pending is None:
        return False, "这条事件已经结算过了"
    event = _find_event(pending.event_id)
    if event is None:
        state.events.pending.remove(pending)
        return False, "事件定义丢失"
    choices = event.get("choices") or []
    if not choices:
        state.events.pending.remove(pending)
        return False, "这条事件没有可选项"

    pick_id = choice_id or pending.default_choice or choices[0]["id"]
    choice = next((c for c in choices if c["id"] == pick_id), None)
    if choice is None:
        return False, "没有这个选项"

    applied = _apply_list(state, choice.get("effects", []), pending.day, Impact())
    for extra in choice.get("emit", []):
        child = _find_event(extra["id"])
        if child:
            _fire_chain(
                state, child, min(7, pending.day + int(extra.get("delay_days", 0))),
                {e["id"]: e for e in load_events()}, Impact(),
            )
    entry = _log_entry(state, pending.log_id)
    if entry is not None:
        entry.effects = applied
        entry.choice = choice["id"]
        entry.choice_label = choice.get("label", "")
        entry.pending = False
    state.events.pending.remove(pending)
    suffix = "" if choice_id else "（超时，按默认处理）"
    return True, f"{choice.get('reply') or choice.get('label', '已处理')}{suffix}"


def expire_pending(state: GameState) -> list[str]:
    """推进到新的一周时，上周没做的决策按默认选项结算。"""
    expired = [p for p in state.events.pending if p.week < state.meta.week]
    msgs = []
    for p in sorted(expired, key=lambda x: (x.week, x.day, x.id)):
        ok, msg = resolve_choice(state, p.id, "")
        if ok:
            msgs.append(f"{p.event_id}：{msg}")
    return msgs


def pending_for_client(state: GameState) -> list[dict]:
    """给前端的待决事件：含正文、说话人、选项与后果预览。"""
    out = []
    for pending in state.events.pending:
        event = _find_event(pending.event_id)
        if event is None:
            continue
        entry = _log_entry(state, pending.log_id)
        out.append({
            "id": pending.id,
            "event_id": pending.event_id,
            "week": pending.week,
            "day": pending.day,
            "title": event["name"],
            "tag": event.get("tag", "team"),
            "speaker": event.get("speaker", ""),
            "narrative": event.get("narrative", ""),
            "stakes": event.get("stakes", ""),
            "art": event.get("art", event.get("tag", "team")),
            "narrative_seen": bool(entry and entry.narrative),
            "choices": [
                {
                    "id": c["id"],
                    "label": c["label"],
                    "hint": c.get("hint", ""),
                    "effects": c.get("preview", []),
                    "default": c["id"] == pending.default_choice,
                }
                for c in event.get("choices", [])
            ],
        })
    return out


# ---------------------------------------------------------------- 重大事件
# V1.0：考核不再是一个独立系统，而是「重大事件」的解决过程。
# 事件只描述现象，不剧透根因；它每周持续冲击全局数据，直到玩家真的
# 部署了有效的东西。没有交卷、没有评分、没有冷却重考。


def _major_resolved(state: GameState, active: dict) -> bool:
    """解决条件只看公司资产：真的部署了什么，而不是「角色学会了什么」。"""
    rule = active.get("resolve") or {}
    kind = rule.get("kind")
    if kind == "index_effective":
        return any(rule.get("query") in i.effective_for for i in state.assets.indexes)
    if kind == "rewrite":
        return rule.get("query") in state.assets.rewritten_queries
    if kind == "fix":
        return rule.get("case_id") in state.assets.fixes
    if kind == "facility":
        return bool(state.assets.facilities.get(rule.get("key")))
    return False


def tick_major(state: GameState, catalog: list[dict] | None = None) -> dict:
    """重大事件：触发 → 每周持续恶化 → 玩家真正解决才解除。"""
    catalog = catalog or load_events()
    majors = [e for e in catalog if e.get("major")]
    active = state.events.active_major
    out: dict = {"error_add": 0.0, "p95_add": 0.0, "trust": 0.0, "tickets": 0, "news": []}

    # 每周最多新开一个重大事件，且同时最多挂两个
    already_opened_this_week = any(a.get("opened_week") == state.meta.week for a in active)
    if not already_opened_this_week and len(active) < 2:
        for e in sorted(majors, key=lambda x: int(x.get("trigger_week", 4))):
            if any(a["id"] == e["id"] for a in active):
                continue
            if state.events.counts.get(e["id"], 0) >= int(e.get("max_per_game", 1)):
                continue
            if state.meta.week < int(e.get("trigger_week", 4)):
                continue
            active.append({
                "id": e["id"],
                "name": e["name"],
                "stakes": e.get("stakes", ""),
                "narrative": e.get("narrative", ""),
                "speaker": e.get("speaker", ""),
                "clues": list(e.get("clues") or []),
                "clue_level": 0,
                "persist": dict(e.get("persist") or {}),
                "resolve": dict(e.get("resolve") or {}),
                "opened_week": state.meta.week,
                "weeks": 0,
            })
            state.events.counts[e["id"]] = state.events.counts.get(e["id"], 0) + 1
            state.events.log.append(EventLogEntry(
                log_id=f"major-{e['id']}-W{state.meta.week}",
                event_id=e["id"], title=e["name"], week=state.meta.week, day=1,
                tag="fault", narrative=e.get("narrative", ""), speaker=e.get("speaker", ""),
            ))
            out["news"].append(f"重大事件：{e['name']}")
            break

    for a in list(active):
        if _major_resolved(state, a):
            active.remove(a)
            state.career_rec.resolved_events.append({
                "id": a["id"], "name": a["name"], "week": state.meta.week,
                "weeks_open": a["weeks"],
            })
            state.org.manager_trust = min(100.0, state.org.manager_trust + 4.0)
            state.ending.timeline.append(f"W{state.meta.week} 解决「{a['name']}」")
            out["news"].append(f"「{a['name']}」已解决，线上恢复")
            continue
        a["weeks"] += 1
        # 拖得越久，冲击越重：这是真实压力，不是「考试失败」的惩罚
        scale = 1.0 + min(1.2, (a["weeks"] - 1) * 0.15)
        p = a["persist"]
        out["error_add"] += float(p.get("error_add", 0.0)) * scale
        out["p95_add"] += float(p.get("p95_add", 0.0)) * scale
        out["trust"] += float(p.get("trust", 0.0)) * scale
        out["tickets"] += int(p.get("tickets", 0))
        state.org.manager_trust = max(0.0, state.org.manager_trust + float(p.get("trust", 0.0)) * scale)
        state.org.tickets += int(p.get("tickets", 0))
    return out


def major_for_client(state: GameState) -> list[dict]:
    out = []
    for a in state.events.active_major:
        out.append({
            "id": a["id"], "name": a["name"], "stakes": a["stakes"],
            "narrative": a["narrative"], "speaker": a["speaker"],
            "weeks": a["weeks"], "clue_level": a["clue_level"],
            "clues": a["clues"][:a["clue_level"]],
            "has_more_clues": a["clue_level"] < len(a["clues"]),
            "persist": a["persist"],
        })
    return out


def consult(state: GameState, major_id: str) -> dict:
    """找同事/技术支持：花调查精力与一点预算，换一条更具体的观察线索。

    线索只描述你看到什么，不告诉你该学什么、该建什么。
    """
    from core.rules import workbench

    a = next((x for x in state.events.active_major if x["id"] == major_id), None)
    if a is None:
        return {"ok": False, "message": "没有这个未解决的重大事件"}
    if a["clue_level"] >= len(a["clues"]):
        return {"ok": False, "message": "能问的都问过了，接下来得自己去实验里查"}
    cost_energy, cost_cash = 8, 30000
    if state.player.energy < cost_energy and not state.meta.sandbox:
        return {"ok": False, "message": f"调查精力不足（需要 {cost_energy}）"}
    if state.company.cash_cents < cost_cash and not state.meta.sandbox:
        return {"ok": False, "message": "预算不够请人帮忙"}
    if not state.meta.sandbox:
        state.player.energy -= cost_energy
        state.company.cash_cents -= cost_cash
    clue = a["clues"][a["clue_level"]]
    a["clue_level"] += 1
    workbench.mark(state, "cs_arch", "seen")
    return {"ok": True, "message": clue, "level": a["clue_level"], "remaining_clues": len(a["clues"]) - a["clue_level"]}
