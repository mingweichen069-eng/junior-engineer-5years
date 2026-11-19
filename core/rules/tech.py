"""科技树：网格化的多路线 + 时间读条 + 考核。

设计要点：
- 六条轨道（基础 / 数据库 / 后端 / SRE / 性能 / 算法），节点用 (col, row) 定位，前端画成 HOI4 式网格。
- 前置支持「全部满足」与「任一满足」；专精与通才用 excludes 互斥，产生真正的取舍。
- 读条**不花行动力**：点「开始读条」后，每周自动推进 1~5 点；
  推进速度 = 1 + 自学精力 // 20，推进 1 点消耗 4 点精力，精力每周自动回复。
  攒满 100 精力冲大节点会明显更快；精力见底时只能每周 1 点慢慢磨。
  跨线第一个节点的总学时会翻倍（基础节点能压低倍数）。
- 读满之后：
    · 简单模式 / 计算机基础节点 → 直接掌握
    · 普通及以上 → 拿到「一次考核机会」，必须在**本周内**交卷
- 考核失败：冷却一周可再来一次；第二次仍失败，读条清零，重新读条。
- 计算机基础（cs 轨道）不解锁能力，但会降低跨线成本、并给模拟层真实修正。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from core.model.state import GameState
from core.rules import exam as exam_rules
from core.rules import difficulty as difficulty_rules

CONTENT = Path(__file__).resolve().parents[2] / "content"

# 专业分支：只有这些分支之间才存在「转向成本」
PRO_BRANCHES = ("db", "backend", "sre", "perf", "algo")

# 模拟层认可的修正键；节点 effects.mods 里出现其它键会被忽略
MOD_KEYS = ("api_overhead", "pool_capacity", "io_wait", "query_time", "contagion", "replica_lag")

# 考核冷却（周）与允许的失败次数
EXAM_COOLDOWN_WEEKS = 1
EXAM_MAX_FAILS = 2

# 自学精力：上限、每 20 点精力换 +1 读条速度（最高 5）、推进 1 点学时消耗的精力
ENERGY_CAP = 100
ENERGY_PER_RATE = 20
ENERGY_PER_POINT = 4
# 行动力 ↔ 自学精力 的牵制：每周前 ENERGY_FREE_ACTIONS 个动作不影响学习，
# 之后每个动作让下周的精力回复少 ENERGY_PER_ACTION 点；动作特别多还会直接烧掉存量精力。
ENERGY_FREE_ACTIONS = 2
ENERGY_PER_ACTION = 2
ENERGY_OVERLOAD_FROM = 5
ENERGY_OVERLOAD_DRAIN = 3


@lru_cache(maxsize=4)
def _load_cached(path_str: str) -> dict:
    return json.loads(Path(path_str).read_text(encoding="utf-8"))


def load(path: Path | None = None) -> dict:
    return _load_cached(str(path or CONTENT / "tech.json"))


def node_def(tech: dict, node_id: str) -> dict | None:
    return next((n for n in tech["nodes"] if n["id"] == node_id), None)


def knowledge_def() -> dict:
    return json.loads((CONTENT / "knowledge.json").read_text(encoding="utf-8"))


def _has(state: GameState, node_id: str) -> bool:
    return bool(state.tech.unlocked.get(node_id))


def req_state(state: GameState, node: dict) -> tuple[bool, list[str]]:
    """前置是否满足，以及缺哪些。requires_any 只要求命中一个。"""
    missing: list[str] = []
    for req in node.get("requires_all", []):
        if not _has(state, req):
            missing.append(req)
    any_req = node.get("requires_any", [])
    if any_req and not any(_has(state, r) for r in any_req):
        missing.append(" / ".join(any_req))
    return (not missing), missing


def conflict(state: GameState, node: dict) -> str:
    """互斥检查：本节点排斥已解锁节点，或已解锁节点排斥本节点。"""
    tech = load()
    mine = set(node.get("excludes", []))
    for other_id, ok in state.tech.unlocked.items():
        if not ok or other_id == node["id"]:
            continue
        if other_id in mine:
            other = node_def(tech, other_id)
            return other["name"] if other else other_id
        other = node_def(tech, other_id)
        if other and node["id"] in other.get("excludes", []):
            return other["name"]
    return ""


def study_cost(state: GameState, node: dict) -> int:
    """读条总点数。跨线第一个节点成本翻倍；计算机基础每解锁一个，把倍数拉低 0.1。"""
    tech = load()
    base = int(node["ap"])
    branch = node["branch"]
    if branch not in PRO_BRANCHES:
        return base
    has_here = any(_has(state, n["id"]) for n in tech["nodes"] if n["branch"] == branch)
    has_else = any(
        _has(state, n["id"]) for n in tech["nodes"] if n["branch"] in PRO_BRANCHES and n["branch"] != branch
    )
    if has_here or not has_else:
        return base
    mult = float(tech.get("switch_cost_multiplier", 2))
    cs_done = sum(1 for n in tech["nodes"] if n["branch"] == "cs" and _has(state, n["id"]))
    mult = max(1.2, mult - 0.1 * cs_done)
    return int(round(base * mult))


def needs_exam(state: GameState, node: dict) -> bool:
    """这局这个节点读满之后要不要考核。

    简单模式读满即得；计算机基础是「知识课」，不需要交作业。
    """
    if not node.get("exam"):
        return False
    if node["branch"] not in PRO_BRANCHES and not node.get("exam_required"):
        return False
    return bool(difficulty_rules.get(state).get("tech_exam", True))


def exam_of(state: GameState, node_id: str) -> dict | None:
    return state.tech.exams.get(node_id)


def study_rate(state: GameState) -> int:
    """当前读条速度：1 + 精力 // 20（上限 5 点/周）。精力见底也能每周 1 点。"""
    return min(5, 1 + max(0, int(state.player.energy)) // ENERGY_PER_RATE)


def energy_penalty(state: GameState) -> int:
    """上周做了太多动作，本周精力回复要扣多少点。"""
    used = int(state.actions.actions_this_week)
    free = ENERGY_FREE_ACTIONS
    return max(0, used - free) * ENERGY_PER_ACTION


def energy_regen(state: GameState) -> int:
    """本周实际能回复的精力（难度基数 minus 动作惩罚）。下限 0，永远不倒扣。"""
    base = int(difficulty_rules.get(state).get("study_regen", 12))
    if state.meta.sandbox:
        return 0
    return max(0, base - energy_penalty(state))


def overload_drain(state: GameState) -> int:
    """动作多到 ENERGY_OVERLOAD_FROM 以上时，额外烧掉的存量精力。"""
    used = int(state.actions.actions_this_week)
    return max(0, used - ENERGY_OVERLOAD_FROM) * ENERGY_OVERLOAD_DRAIN


def node_status(state: GameState, node: dict) -> dict:
    cost = study_cost(state, node)
    progress = int(state.tech.progress.get(node["id"], 0))
    unlocked = _has(state, node["id"])
    ok, missing = req_state(state, node)
    blocked = conflict(state, node)
    exam = exam_of(state, node["id"])
    exam_state = ""
    exam_until = 0
    fails = 0
    if exam:
        exam_state = str(exam.get("state", "open"))
        exam_until = int(exam.get("until", 0))
        fails = int(exam.get("fails", 0))
    cooling = exam_state == "cool" and state.meta.week < exam_until
    exam_open = exam_state == "open"
    return {
        "progress": progress,
        "ap": cost,
        "base_ap": int(node["ap"]),
        "remaining": max(0, cost - progress),
        "full": progress >= cost,
        "ready": exam_open and not unlocked,
        "exam_state": exam_state,
        "exam_until": exam_until,
        "exam_fails": fails,
        "cooling": cooling,
        "examined": needs_exam(state, node),
        "unlocked": unlocked,
        "req_ok": ok and not blocked,
        "requires": missing,
        "requires_all": node.get("requires_all", []),
        "requires_any": node.get("requires_any", []),
        "excluded_by": blocked,
        "knowledge": node.get("knowledge", []),
        "switch_penalty": cost > int(node["ap"]),
        "studying": state.tech.active == node["id"],
        "rate": study_rate(state),
    }


def _learn(state: GameState, node: dict) -> None:
    for kid in node.get("knowledge", []):
        state.tech.knowledge[kid] = True


def _apply_effects(state: GameState, node: dict) -> None:
    eff = node.get("effects", {})
    for skill, amount in (eff.get("skill") or {}).items():
        state.player.skills[skill] = min(100.0, state.player.skills.get(skill, 0.0) + float(amount))
    for trait, amount in (eff.get("traits") or {}).items():
        state.player.traits[trait] = min(100.0, state.player.traits.get(trait, 0.0) + float(amount))
    if eff.get("capstone"):
        state.ending.flavor = str(eff["capstone"])
    if eff.get("mastery"):
        state.tech.mastery = str(eff["mastery"])
    if eff.get("ap_bonus"):
        state.tech.ap_bonus += int(eff["ap_bonus"])
    if eff.get("docs"):
        state.actions.docs += int(eff["docs"])


def unlock(state: GameState, node: dict) -> None:
    """真正掌握：写解锁位、记方向、记知识、结算节点效果。"""
    if state.tech.active == node["id"]:
        state.tech.active = ""
    state.tech.unlocked[node["id"]] = True
    if node["branch"] in PRO_BRANCHES:
        state.tech.direction = node["branch"]
    _learn(state, node)
    _apply_effects(state, node)
    state.tech.exams.pop(node["id"], None)


def start_exam(state: GameState, node: dict) -> None:
    state.tech.exams[node["id"]] = {
        "state": "open",
        "week": state.meta.week,
        "until": state.meta.week,
        "fails": 0,
    }


def _study_guard(state: GameState, node: dict | None, node_id: str) -> tuple[bool, str]:
    """开始/继续读条的前置检查。返回 (能不能学, 原因)。"""
    tech = load()
    if node is None:
        return False, "没有这个节点"
    if _has(state, node_id):
        return False, "已经掌握"
    blocked = conflict(state, node)
    if blocked:
        return False, f"与已选路线「{blocked}」互斥，本局不能再走这条"
    ok, missing = req_state(state, node)
    if not ok:
        names = [node_def(tech, m) for m in missing]
        label = "、".join(n["name"] if n else m for n, m in zip(names, missing))
        return False, f"前置未满足：{label}"

    exam = exam_of(state, node_id)
    if exam:
        if exam.get("state") == "cool":
            if state.meta.week < int(exam.get("until", 0)):
                return False, f"考核冷却中：第 {exam.get('until')} 周可以再考一次"
            return False, "冷却结束，去参加考核（不用重新读条）"
        return False, "读条已经完成，去参加考核"

    cost = study_cost(state, node)
    if int(state.tech.progress.get(node_id, 0)) >= cost:
        return False, "读条已经完成，去参加考核"
    return True, ""


def toggle_study(state: GameState, node_id: str) -> tuple[bool, str]:
    """开始读条 / 暂停读条。读条不花行动力，只随时间推进（每周结算时走条）。

    同时只能有一个在读节点：切换到别的节点会暂停当前节点（进度保留）。
    """
    tech = load()
    node = node_def(tech, node_id)
    if node is None:
        return False, "没有这个节点"
    if state.tech.active == node_id:
        state.tech.active = ""
        return True, f"暂停了「{node['name']}」（进度保留）"
    ok, why = _study_guard(state, node, node_id)
    if not ok:
        return False, why
    prev = state.tech.active
    state.tech.active = node_id
    # 第一次投入就把知识记为已读：读懂节点要会什么，本身就算一半的学习
    if int(state.tech.progress.get(node_id, 0)) == 0:
        _learn(state, node)
    rate = study_rate(state)
    eta = (study_cost(state, node) - int(state.tech.progress.get(node_id, 0)) + rate - 1) // rate
    prefix = f"（放下了「{node_def(tech, prev)['name']}」）" if prev else ""
    return True, f"开始读「{node['name']}」{prefix}：按当前精力每周 +{rate} 点，预计 {eta} 周读满"


def tick_study(state: GameState) -> list[str]:
    """每周结算：先回复自学精力，再推进在读节点；读满按难度分流（直接掌握 / 进入考核）。

    推进 1 点学时消耗 ENERGY_PER_POINT 点精力；精力不足时照样推进，但只按现有精力给速度。
    """
    from core.model.state import EventLogEntry

    msgs: list[str] = []
    if not state.meta.sandbox:
        regen = energy_regen(state)
        drain = overload_drain(state)
        state.player.energy = max(0.0, min(ENERGY_CAP, state.player.energy + regen - drain))
        if regen <= 0 and state.actions.actions_this_week > ENERGY_FREE_ACTIONS:
            msgs.append(
                f"上周连做了 {state.actions.actions_this_week} 个动作，这周一点精力都没回过来："
                "救火把学习时间挤光了。"
            )

    node_id = state.tech.active
    if not node_id:
        return msgs
    tech = load()
    node = node_def(tech, node_id)
    if node is None or _has(state, node_id):
        state.tech.active = ""
        return msgs
    exam = exam_of(state, node_id)
    if exam and exam.get("state") == "open":
        state.tech.active = ""
        return msgs

    cost = study_cost(state, node)
    progress = int(state.tech.progress.get(node_id, 0))
    if progress >= cost:
        state.tech.active = ""
        return msgs

    if state.meta.sandbox:
        rate = cost - progress
        spend = 0
    else:
        rate = study_rate(state)
        spend = min(int(state.player.energy), rate * ENERGY_PER_POINT)
        state.player.energy = max(0.0, state.player.energy - spend)
    progress = min(cost, progress + rate)
    state.tech.progress[node_id] = progress

    def _log(title: str, narrative: str) -> None:
        state.events.log.append(
            EventLogEntry(
                log_id=f"study-{node_id}-{state.meta.week}",
                event_id=f"study:{node_id}",
                title=title,
                week=state.meta.week,
                day=7,
                tag="team",
                narrative=narrative,
            )
        )

    if progress < cost:
        return msgs

    state.tech.active = ""
    if needs_exam(state, node):
        start_exam(state, node)
        msg = f"读条完成：{node['name']} 拿到一次考核机会（本周内交卷，否则算一次未通过）"
        _log(f"读条完成：{node['name']}", "这周得抽时间把考核交了，拖到下周就算一次未通过。")
    else:
        unlock(state, node)
        msg = f"读条完成：{node['name']} 已掌握"
        _log(f"掌握：{node['name']}", node.get("unlocks_note", ""))
    msgs.append(msg)
    return msgs


def _fail(state: GameState, node: dict, reason: str, cooldown: bool = True) -> tuple[bool, str]:
    """记一次失败：第一次冷却一周，第二次清零读条。

    超时（跨周未交卷）已经等过一周了，所以不再额外冷却。
    """
    exam = exam_of(state, node["id"]) or {"state": "open", "week": state.meta.week, "until": state.meta.week, "fails": 0}
    state.tech.exams_failed += 1
    fails = int(exam.get("fails", 0)) + 1
    if fails >= EXAM_MAX_FAILS:
        state.tech.exams.pop(node["id"], None)
        state.tech.progress[node["id"]] = 0
        if state.tech.active == node["id"]:
            state.tech.active = ""
        return False, f"考核未通过（{reason}）。这已经是第二次，读条清零，重新读一遍再来。"
    wait = EXAM_COOLDOWN_WEEKS if cooldown else 0
    until = state.meta.week + wait
    state.tech.exams[node["id"]] = {
        "state": "cool",
        "week": state.meta.week,
        "until": until,
        "fails": fails,
    }
    if wait:
        return False, (
            f"考核未通过（{reason}）。冷却 {EXAM_COOLDOWN_WEEKS} 周，"
            f"第 {until} 周可以再考一次；再失败读条清零。"
        )
    return False, f"考核未通过（{reason}）。这次算一次失败，本周可以再考一次；再失败读条清零。"


def submit_exam(state: GameState, node_id: str, answer: str) -> tuple[bool, str, dict]:
    """交卷。返回 (通过与否, 文案, 判题细节)。"""
    tech = load()
    node = node_def(tech, node_id)
    if node is None:
        return False, "没有这个节点", {}
    if _has(state, node_id):
        return False, "已经掌握", {}

    exam = exam_of(state, node_id)
    if exam is None:
        return False, "读条还没完成，先去研究", {}
    if exam.get("state") == "cool" and state.meta.week < int(exam.get("until", 0)):
        return False, f"冷却中：第 {exam.get('until')} 周可以再考一次", {}
    # 冷却结束 → 重新开考（不重置失败次数，第二次失败就清零）
    exam = dict(exam)
    exam["state"] = "open"
    exam["week"] = state.meta.week
    exam["until"] = state.meta.week
    state.tech.exams[node_id] = exam

    task = exam_rules.task_for(node, state)
    if task is None:
        # 没有配题：读满即得，避免卡死
        unlock(state, node)
        return True, f"{node['name']} 已掌握", {"kind": "none"}

    ok, why, detail = exam_rules.validate(task, answer)
    detail = dict(detail or {})
    detail["fails"] = int(exam.get("fails", 0))
    if ok:
        state.tech.exams_passed += 1
        unlock(state, node)
        detail["solution"] = task.get("solution", "")
        return True, f"考核通过：{node['name']} 已掌握", detail
    ok2, msg = _fail(state, node, why)
    detail["fails"] = int(exam.get("fails", 0)) + 1
    detail["hint"] = task.get("hint", "")
    return ok2, msg, detail


def expire_exams(state: GameState) -> list[str]:
    """跨周：上周没交卷的考核算一次未通过。世界不会等你。"""
    msgs: list[str] = []
    tech = load()
    for node_id, exam in list(state.tech.exams.items()):
        if exam.get("state") != "open":
            continue
        if int(exam.get("week", 0)) >= state.meta.week:
            continue
        node = node_def(tech, node_id)
        if node is None:
            state.tech.exams.pop(node_id, None)
            continue
        _ok, msg = _fail(state, node, "上周没有交卷", cooldown=False)
        msgs.append(f"{node['name']}：{msg}")
    return msgs


def mods(state: GameState) -> dict[str, float]:
    """把所有已解锁节点的 mods 连乘，得到模拟层的修正系数。"""
    out = {k: 1.0 for k in MOD_KEYS}
    tech = load()
    by_id = {n["id"]: n for n in tech["nodes"]}
    for node_id, unlocked in state.tech.unlocked.items():
        if not unlocked:
            continue
        node = by_id.get(node_id)
        if not node:
            continue
        for key, value in (node.get("effects", {}).get("mods") or {}).items():
            if key in out:
                out[key] *= float(value)
    for key, value in out.items():
        out[key] = max(0.5, min(2.0, value))
    return out


def trait_totals(state: GameState) -> dict[str, float]:
    return {k: round(v, 1) for k, v in state.player.traits.items() if v > 0}


def knowledge_rows(state: GameState) -> list[dict]:
    """知识库：按分支列出条目，标注是否已读、是否已掌握。"""
    data = knowledge_def()
    items = data["items"]
    fields = data["fields"]
    tech = load()
    node_of: dict[str, str] = {}
    for node in tech["nodes"]:
        for kid in node.get("knowledge", []):
            node_of.setdefault(kid, node["id"])
    rows = []
    for kid, item in items.items():
        node_id = node_of.get(kid, "")
        read = bool(state.tech.knowledge.get(kid))
        rows.append({
            "id": kid,
            "title": item["title"],
            "field": item.get("field", ""),
            "field_label": fields.get(item.get("field", ""), item.get("field", "")),
            "branch": item.get("branch", ""),
            "summary": item.get("summary", ""),
            # 没读过的知识只给标题和摘要：留一点「去学」的动力，也不剧透结论
            "body": item.get("body", "") if read else "",
            "in_game": item.get("in_game", "") if read else "",
            "tell": item.get("tell", "") if read else "",
            "read": read,
            "locked": not read,
            "mastered": bool(read and node_id and state.tech.unlocked.get(node_id)),
            "node": node_id,
        })
    order = {"cs": 0, "db": 1, "backend": 2, "sre": 3, "perf": 4, "algo": 5}
    rows.sort(key=lambda r: (order.get(r["branch"], 9), r["title"]))
    return rows


def pending_exams(state: GameState) -> list[dict]:
    out = []
    tech = load()
    for node_id, exam in state.tech.exams.items():
        node = node_def(tech, node_id)
        if node is None:
            continue
        out.append({
            "node": node_id,
            "name": node["name"],
            "state": exam.get("state", "open"),
            "until": int(exam.get("until", 0)),
            "fails": int(exam.get("fails", 0)),
            "week": int(exam.get("week", 0)),
        })
    return out


def capstone_branch(state: GameState) -> str:
    return state.ending.flavor
