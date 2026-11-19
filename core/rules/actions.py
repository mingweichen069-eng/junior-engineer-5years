"""玩家动作：校验、扣资源、延迟生效。

每个动作都必须**直接改变系统**：改结构、改容量、改配置、还债。
"只为了解锁另一个动作"的动作（刷技能、刷准备标记）已经删掉——
技能只能从科技树学，科技节点要读条 + 考核。

行动力：每周基数 24（见 difficulty），科技节点与事件都能加减。
"""

from __future__ import annotations

import json
from pathlib import Path

from core.model.state import CachePolicy, GameState, Index, PendingEffect

CONTENT = Path(__file__).resolve().parents[2] / "content"

# 技能成长：每次"相关动作"给对应技能加经验（设计文档：约 6~8 次行动达标）
SKILL_GAIN = 3.5
SKILL_GAIN_BY_ACTION = {
    "add_index_user_created": {"db": SKILL_GAIN, "sql": 1.0},
    "cache_dashboard": {"backend": SKILL_GAIN},
    "rewrite_dashboard": {"sql": SKILL_GAIN * 1.5},
    "add_server": {"ops": SKILL_GAIN},
    "pool_60": {"db": SKILL_GAIN, "backend": 1.0},
    "open_monitoring": {"ops": SKILL_GAIN},
    "write_docs": {"backend": 1.0},
    "study_sql": {"sql": 10.0},     # 设计文档：约 2~3 次专注研究就能改写大屏 SQL
    "study_db": {"db": 8.0},
    "study_ops": {"ops": 8.0},
    "load_test": {"ops": 2.0},
    "audit_cache": {"backend": 1.0},
    "archive_orders": {"db": 1.0},
    "rewrite_pending": {"sql": 2.0},
    "consolidate_servers": {"ops": 1.0},
    "add_read_replica": {"db": 1.0, "ops": 1.0},
}


def load_actions(path: Path | None = None) -> list[dict]:
    return json.loads((path or CONTENT / "actions.json").read_text(encoding="utf-8"))


def _find(actions: list[dict], action_id: str) -> dict | None:
    return next((a for a in actions if a["id"] == action_id), None)


def is_repeatable(action: dict) -> bool:
    return action.get("repeatable") is True


def ap_cap(state: GameState) -> int:
    """本周行动力上限 = 难度基数 + 事件永久加成 + 事件临时加成（不再有「学会科技加 AP」）。"""
    from core.rules import difficulty as difficulty_rules

    cap = int(difficulty_rules.get(state).get("ap_base", 24))
    cap += int(state.player.ap_bonus_event)
    if state.player.ap_temp_until and state.meta.week <= state.player.ap_temp_until:
        cap += int(state.player.ap_temp_bonus)
    return max(1, cap)


def current_cost(state: GameState, action: dict) -> int:
    """可重复动作的成本随次数递增（每次 +1 AP，最多加到基数的两倍）。"""
    base = int(action["ap"])
    if not is_repeatable(action):
        return base
    used = state.actions.counts.get(action["id"], 0)
    return min(base * 2, base + used)


def check(state: GameState, action: dict) -> tuple[bool, str]:
    if state.ending.ended and not state.meta.sandbox:
        return False, "游戏已经结束"
    if not is_repeatable(action) and state.actions.applied.get(action["id"]):
        return False, "已经做过这个动作"
    cost = current_cost(state, action)
    if not state.meta.sandbox and state.player.ap < cost:
        return False, f"行动力不足（需要 {cost}，剩余 {state.player.ap}）"
    if not state.meta.sandbox and state.company.cash_cents < action.get("cash_cents", 0):
        return False, "现金不足"
    for skill, need in (action.get("requires", {}) or {}).get("skill", {}).items():
        have = state.player.skills.get(skill, 0.0)
        if have < need:
            return False, f"{skill} 技能不足（需要 {need}，当前 {have:.0f}）"
    # V1.0：不再有任何「需要先学会某科技」的门槛。
    # 玩家随时可以直接尝试；做错了由系统按物理结果算代价，而不是被挡在门外。
    return True, ""


def start(state: GameState, action_id: str, actions: list[dict] | None = None) -> tuple[bool, str]:
    actions = actions or load_actions()
    action = _find(actions, action_id)
    if action is None:
        return False, "没有这个动作"
    ok, reason = check(state, action)
    if not ok:
        return False, reason

    cost = current_cost(state, action)
    if not state.meta.sandbox:
        state.player.ap -= cost
        state.company.cash_cents -= action.get("cash_cents", 0)
    state.actions.applied[action_id] = True
    state.actions.counts[action_id] = state.actions.counts.get(action_id, 0) + 1
    # 一周内做的动作越多，下周自学精力回复越少：救火和学习抢的是同一份心力
    state.actions.actions_this_week += 1
    _gain_skills(state, action_id)

    due = state.meta.week + int(action.get("weeks", 0))
    for effect in action["effects"]:
        if due <= state.meta.week:
            _apply_effect(state, effect)
        else:
            state.actions.pending.append(PendingEffect(action_id=action_id, due_week=due, effect=effect))
    suffix = f"（预计 W{due} 生效）" if due > state.meta.week else ""
    return True, f"已开始：{action['name']}{suffix}"


def _gain_skills(state: GameState, action_id: str) -> None:
    for skill, amount in SKILL_GAIN_BY_ACTION.get(action_id, {}).items():
        state.player.skills[skill] = min(100.0, state.player.skills.get(skill, 0.0) + amount)


def apply_due(state: GameState) -> list[str]:
    """在每周结算开头应用到期效果。"""
    applied: list[str] = []
    remaining = []
    for item in state.actions.pending:
        if item.due_week <= state.meta.week:
            _apply_effect(state, item.effect)
            applied.append(item.action_id)
        else:
            remaining.append(item)
    state.actions.pending = remaining
    return applied


def _sync_assets(state: GameState, effect: dict) -> None:
    """任何部署类动作都同步登记到公司资产。

    数值修正只读资产，不读「角色学会了什么」；这条同步让快捷行动与工作台部署走同一套物理。
    """
    from core.model.state import DeployedIndex
    from core.rules import workbench

    kind = effect["type"]
    if kind in ("add_index", "add_index_online"):
        name, table = effect["name"], effect["table"]
        cols = list(effect["columns"])
        if not any(i.name == name for i in state.assets.indexes):
            state.assets.indexes.append(DeployedIndex(
                table=table, columns=cols, name=name,
                effective_for=workbench.effective_for(state, table, cols),
                week=state.meta.week,
            ))
        workbench.mark(state, "db_t1", "deployed")
    elif kind == "rewrite_query":
        qid = effect["query_id"]
        if qid not in state.assets.rewritten_queries:
            state.assets.rewritten_queries.append(qid)
        for di in state.assets.indexes:
            di.effective_for = workbench.effective_for(state, di.table, di.columns)
        workbench.mark(state, "db_t2", "deployed")
    elif kind == "add_replica":
        state.assets.facilities["replica"] = state.assets.facilities.get("replica", 0) + 1
        workbench.mark(state, "db_replica", "deployed")
    elif kind == "circuit_breaker":
        state.assets.facilities["circuit_breaker"] = 1
        workbench.mark(state, "be_api", "deployed")
    elif kind == "enable_cache":
        state.assets.facilities["cache"] = 1
    elif kind == "monitoring":
        state.assets.facilities["monitoring"] = 1
    elif kind == "docs":
        state.assets.docs += 1


def _apply_effect(state: GameState, effect: dict) -> None:
    _sync_assets(state, effect)
    kind = effect["type"]
    if kind == "add_index":
        table = state.db.tables[effect["table"]]
        table.indexes[effect["name"]] = Index(columns=list(effect["columns"]))
        if effect.get("online"):
            table.indexes[effect["name"]].bloat = min(0.35, table.indexes[effect["name"]].bloat + 0.05)
        else:
            # 朴素 DDL：大表建索引期间锁写，当周出现一次短时锁冲击
            build_seconds = table.rows / 30000
            state.flags["lock_week"] = state.meta.week
            state.flags["lock_ms"] = min(5000.0, build_seconds * 45)
    elif kind == "add_index_online":
        table = state.db.tables[effect["table"]]
        table.indexes[effect["name"]] = Index(columns=list(effect["columns"]), bloat=0.05)
    elif kind == "enable_cache":
        state.cache.enabled = True
        qid = effect["query_id"]
        state.cache.policies[qid] = CachePolicy(
            endpoint=qid, ttl_s=int(effect["ttl_s"]), invalidate_on_write=False
        )
    elif kind == "rewrite_query":
        state.flags[f"{effect['query_id']}_rewritten"] = True
    elif kind == "add_servers":
        state.app.servers += int(effect["count"])
    elif kind == "add_pool":
        state.app.pool_size = min(state.db.max_connections, state.app.pool_size + int(effect["delta"]))
    elif kind == "set_pool":
        state.app.pool_size = int(effect["size"])
    elif kind == "monitoring":
        state.actions.monitoring = True
    elif kind == "docs":
        # 文档收益递减：第一次 +2 信任，第二次 +1，之后不再涨——
        # 防止"每周补文档"变成无脑最优解。
        gain = max(0, 2 - state.actions.docs)
        state.actions.docs += 1
        if gain:
            state.org.manager_trust = min(100.0, state.org.manager_trust + gain)
    elif kind == "spawn_debt":
        from core.rules import debt as debt_rules

        debt_rules.spawn(
            state,
            effect["debt_id"],
            effect.get("category", "query"),
            float(effect.get("severity", 2.0)),
            float(effect.get("growth", 0.02)),
            effect.get("name", effect["debt_id"]),
        )
    elif kind == "archive_orders":
        orders = state.db.tables["orders"]
        orders.rows = max(100_000, int(orders.rows * 0.85))
        state.db.tables["order_items"].rows = max(300_000, int(state.db.tables["order_items"].rows * 0.85))
    elif kind == "consolidate_servers":
        state.app.servers = max(2, state.app.servers - 1)
        from core.rules import debt as debt_rules

        debt_rules.resolve(state, "server_sprawl")
    elif kind == "promo_prepared":
        state.flags["promo_prepared"] = True
    elif kind == "add_replica":
        state.app.read_replicas = min(3, state.app.read_replicas + 1)
    elif kind == "backup":
        state.actions.backup = True
        from core.rules import debt as debt_rules

        debt_rules.resolve(state, "no_backup")
    elif kind == "circuit_breaker":
        from core.rules import debt as debt_rules

        debt_rules.resolve(state, "no_circuit_breaker")
    elif kind == "resolve_debt":
        from core.rules import debt as debt_rules

        debt_rules.resolve(state, effect["debt_id"])
    elif kind == "trust":
        state.org.manager_trust = max(0.0, min(100.0, state.org.manager_trust + float(effect["value"])))
    elif kind == "db_upgrade":
        # 升配：更大的 buffer pool + 更多连接。命中率上升，池子也允许更大
        state.db.buffer_pool_gb = round(state.db.buffer_pool_gb + float(effect.get("buffer_gb", 4)), 1)
        state.db.max_connections += int(effect.get("max_connections", 100))
    elif kind == "expand_disk":
        state.db.disk_capacity_gb = round(state.db.disk_capacity_gb + float(effect.get("capacity_gb", 200)), 1)
        state.db.disk_iops_limit += int(effect.get("iops", 4000))
    elif kind == "ap_bonus":
        # 一次性动作也能永久加行动力（比如把重复劳动自动化掉）
        state.player.ap_bonus_event += int(effect["value"])
    elif kind == "vacuum_index":
        table = state.db.tables[effect["table"]]
        for idx in table.indexes.values():
            idx.bloat = 0.0
    else:  # pragma: no cover
        raise ValueError(f"未知效果类型 {kind}")


def reset_ap(state: GameState) -> None:
    if state.meta.sandbox:
        state.player.ap = 999
    else:
        state.player.ap = ap_cap(state)
