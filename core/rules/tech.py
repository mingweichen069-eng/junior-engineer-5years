"""技术档案与调查精力。

V1.0 起这里不再有任何「解锁 / 前置 / 读条 / 考核 / 冷却」。
知识不是奖励，是工具：玩家随时可以直接使用任何技术，
技术档案只记录他接触、实验、部署过什么，以及在哪一周出过事故。

调查精力是「认知预算」，用于工作台的实验、查阅、咨询；它不解锁任何东西。
动作越多，下周回复越少（救火挤掉调查），这条牵制保留。
"""

from __future__ import annotations

import json
from pathlib import Path

from core.model.state import GameState
from core.rules import difficulty as difficulty_rules

CONTENT = Path(__file__).resolve().parents[2] / "content"
PRO_BRANCHES = ("db", "backend", "sre", "perf", "algo")
MOD_KEYS = ("api_overhead", "pool_capacity", "io_wait", "query_time", "contagion", "replica_lag")

ENERGY_CAP = 100
ENERGY_FREE_ACTIONS = 2
ENERGY_PER_ACTION = 2
ENERGY_OVERLOAD_FROM = 5
ENERGY_OVERLOAD_DRAIN = 3

_CACHE: dict[str, dict] = {}


def _load_cached(path_str: str) -> dict:
    if path_str not in _CACHE:
        _CACHE[path_str] = json.loads(Path(path_str).read_text(encoding="utf-8"))
    return _CACHE[path_str]


def load(path: Path | None = None) -> dict:
    return _load_cached(str(path or CONTENT / "tech.json"))


def node_def(tech: dict, node_id: str) -> dict | None:
    return next((n for n in tech["nodes"] if n["id"] == node_id), None)


def knowledge_def() -> dict:
    return _load_cached(str(CONTENT / "knowledge.json"))


# ---------------------------------------------------------------- 精力预算


def energy_penalty(state: GameState) -> int:
    used = int(state.actions.actions_this_week)
    return max(0, used - ENERGY_FREE_ACTIONS) * ENERGY_PER_ACTION


def energy_regen(state: GameState) -> int:
    base = int(difficulty_rules.config(state.meta.difficulty).get("study_regen", 12))
    if state.meta.sandbox:
        return 0
    return max(0, base - energy_penalty(state))


def overload_drain(state: GameState) -> int:
    used = int(state.actions.actions_this_week)
    return ENERGY_OVERLOAD_DRAIN if used >= ENERGY_OVERLOAD_FROM else 0


def tick_energy(state: GameState) -> list[str]:
    """每周结算调查精力：回复 - 动作惩罚 - 超载消耗。不再推进任何读条。"""
    if state.meta.sandbox:
        return []
    regen = energy_regen(state)
    drain = overload_drain(state)
    state.player.energy = max(0.0, min(ENERGY_CAP, state.player.energy + regen - drain))
    out: list[str] = []
    if energy_penalty(state):
        out.append(f"本周救火 {state.actions.actions_this_week} 次，调查精力回复被挤掉 {energy_penalty(state)} 点")
    if drain:
        out.append("连续高强度救火，存量精力也被烧掉了一些")
    return out


# ---------------------------------------------------------------- 视图 / 数值


def mods(state: GameState) -> dict[str, float]:
    """修正系数：唯一来源是公司资产（真的部署了什么），不是档案或考试。"""
    from core.rules import workbench

    return workbench.mods(state)


def trait_totals(state: GameState) -> dict[str, float]:
    return {k: round(v, 1) for k, v in state.player.traits.items() if v > 0}


def knowledge_rows(state: GameState) -> list[dict]:
    """内部知识库：所有条目全部可读、可搜索。

    以前未读卡会隐藏正文「留一点去学的动力」——那是把知识当奖励发。
    现在它是工程师随手可查的文档，价值在于帮你解释你观察到的现象。
    """
    data = knowledge_def()
    items = data["items"]
    fields = data["fields"]
    rows = []
    for kid, item in items.items():
        rows.append({
            "id": kid,
            "title": item["title"],
            "field": item.get("field", ""),
            "field_label": fields.get(item.get("field", ""), item.get("field", "")),
            "branch": item.get("branch", ""),
            "summary": item["summary"],
            "body": item.get("body", ""),
            "in_game": item.get("in_game", ""),
            "tell": item.get("tell", ""),
            "read": bool(state.tech.knowledge.get(kid)),
            "locked": False,
        })
    order = {"cs": 0, "db": 1, "backend": 2, "sre": 3, "perf": 4, "algo": 5}
    rows.sort(key=lambda r: (order.get(r["branch"], 9), r["title"]))
    return rows


def archive_rows(state: GameState) -> list[dict]:
    from core.rules import workbench

    return workbench.archive_rows(state)


def capstone_branch(state: GameState) -> str:
    """档案里最近一次「部署」发生在哪条轨道（只用于文案，不影响任何能力）。"""
    data = load()
    by_id = {n["id"]: n for n in data["nodes"]}
    best_week, best = -1, ""
    for nid, e in state.tech.entries.items():
        if not e.get("deployed"):
            continue
        w = max(e.get("weeks") or [0])
        if w > best_week:
            best_week, best = w, (by_id.get(nid) or {}).get("branch", "")
    return best
