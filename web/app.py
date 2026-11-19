"""JSON API 与页面入口。"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from flask import Flask, jsonify, render_template, request

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.engine import new_game, step  # noqa: E402
from core.rules import actions as action_rules  # noqa: E402
from core.rules import constants as C  # noqa: E402
from core.rules import debt as debt_rules, diagnose as diagnose_rules  # noqa: E402
from core.rules import events as event_rules  # noqa: E402
from core.rules import tech as tech_rules  # noqa: E402
from core.rules import workbench as workbench_rules  # noqa: E402
from core.rules.evaluate import evaluate_peak  # noqa: E402
from core.rules import exam as exam_rules  # noqa: E402
from core.rules import difficulty as difficulty_rules  # noqa: E402
from services import save as saves  # noqa: E402

CURRENT_SLOT = os.environ.get("GAME_SLOT", "web")


def career_skill(st) -> float:  # noqa: ANN001
    from core.rules import career

    return career.skill_level(st)


def month_of(week: int) -> int:
    return (week - 1) // C.WEEKS_PER_MONTH + 1


def _tech_snapshot(st) -> dict:  # noqa: ANN001
    """技术档案：六轨道只是分类。没有前置、读条、考核、解锁，也不 gate 任何操作。"""
    data = tech_rules.load()
    branches = data["branches"]
    rows = tech_rules.archive_rows(st)
    by_id = {n["id"]: n for n in data["nodes"]}
    nodes = []
    for r in rows:
        n = by_id.get(r["id"], {})
        branch = branches.get(r["branch"], {})
        state = ("deployed" if r["deployed"] else "experimented" if r["experimented"]
                 else "seen" if r["seen"] else "untouched")
        nodes.append({
            "id": r["id"], "branch": r["branch"], "tier": r["tier"],
            "col": n.get("col", r["tier"]), "sub": n.get("sub", 0), "row": branch.get("row", 0),
            "name": r["name"], "desc": r["desc"],
            "seen": r["seen"], "experimented": r["experimented"],
            "deployed": r["deployed"], "incident": r["incident"],
            "weeks": r["weeks"], "state": state,
        })
    return {
        "branches": branches,
        "nodes": nodes,
        "layout": data.get("layout", {"min_col": 1, "max_col": 6}),
        "deployed": sum(1 for r in rows if r["deployed"]),
        "experimented": sum(1 for r in rows if r["experimented"]),
        "total": len(rows),
    }


def snapshot(st) -> dict:  # noqa: ANN001
    m = evaluate_peak(st)
    catalog = action_rules.load_actions()
    blocked = []
    if not st.actions.monitoring:
        blocked.append("还没开监控：查询明细被遮住了。你只能看到聚合 p95，看不到是哪条查询在拖后腿。开监控需要 1 AP + ¥300/月。")
    acts = []
    for a in catalog:
        ok, why = action_rules.check(st, a)
        times = st.actions.counts.get(a["id"], 0)
        acts.append({
            "id": a["id"], "name": a["name"], "desc": a.get("desc", ""),
            "ap": action_rules.current_cost(st, a), "cash": a["cash_cents"] / 100,
            "weeks": a.get("weeks", 0),
            "repeatable": bool(a.get("repeatable")),
            "times": times,
            "ready": ok, "reason": why,
        })
    return {
        "week": st.meta.week,
        "month": month_of(st.meta.week),
        "year": (st.meta.week - 1) // 48 + 1,
        "week_in_month": (st.meta.week - 1) % C.WEEKS_PER_MONTH + 1,
        "difficulty": st.meta.difficulty,
        "rules_version": st.meta.rules_version,
        "save_version": st.meta.save_version,
        "sandbox": st.meta.sandbox,
        "bottleneck": diagnose_rules.diagnose(st, m),
        "cash": round(st.company.cash_cents / 100),
        "registered": st.company.registered,
        "ap": st.player.ap,
        "ap_cap": action_rules.ap_cap(st),
        "energy": int(st.player.energy),
        "energy_cap": tech_rules.ENERGY_CAP,
        "energy_regen": int(difficulty_rules.get(st).get("study_regen", 12)),
        "energy_penalty": tech_rules.energy_penalty(st),
        "energy_regen_eff": tech_rules.energy_regen(st),
        "energy_overload": tech_rules.overload_drain(st),
        "actions_this_week": int(st.actions.actions_this_week),
        "review_week": int(st.meta.review_week),
        "review_done": bool(st.meta.review_done),
        "review_pending": bool(st.review.pending),
        "review_continued": bool(st.review.continued),
        "review_report": st.review.last,
        "review_count": len(st.review.reports),
        "review_milestones": list(st.review.milestones),
        "event_gap_weeks": int(difficulty_rules.get(st).get("event_gap_weeks", 3)),
        "last_event_week": int(st.events.last_event_week),
        "ap_bonus": {
            "base": int(difficulty_rules.get(st).get("ap_base", 24)),
            "tech": 0,
            "event": int(st.player.ap_bonus_event),
            "temp": int(st.player.ap_temp_bonus) if st.player.ap_temp_until >= st.meta.week else 0,
            "temp_until": int(st.player.ap_temp_until),
        },
        "metrics": {
            "peak_qps": round(m.peak_qps, 1),
            "p95": round(m.p95_ms, 1),
            "availability": round(m.availability * 100, 2),
            "error_rate": round(m.error_rate * 100, 2),
            "io_util": round(m.io_util * 100, 1),
            "iops": round(m.io_iops),
            "db_qps": round(m.db_qps, 1),
            "pool_rho": round(m.pool_rho, 2),
            "buffer_hit": round(m.buffer_hit * 100, 1),
            "disk_gb": round(m.disk_used_gb, 2),
            "disk_pct": round(m.disk_used_gb / st.db.disk_capacity_gb * 100, 1),
        },
        "queries": [
            {"id": q.id, "name": q.name, "qps": round(q.qps, 2),
             "ms": round(q.time_ms, 1) if st.actions.monitoring else None,
             "rows": q.rows if st.actions.monitoring else None,
             "path": q.path if st.actions.monitoring else None,
             "hidden": not st.actions.monitoring}
            for q in m.queries
        ],
        "tables": [
            {"name": t.name, "rows": t.rows, "indexes": list(t.indexes)}
            for t in st.db.tables.values()
        ],
        "app": {
            "servers": st.app.servers, "workers": st.app.workers_per_server,
            "pool": st.app.pool_size,
        },
        "cache": {"enabled": st.cache.enabled, "policies": list(st.cache.policies)},
        "actions": acts,
        "events": [
            {"id": e.log_id, "event": e.event_id, "title": e.title, "week": e.week,
             "day": e.day, "cause": e.cause_id, "depth": e.depth, "effects": e.effects,
             "tag": e.tag, "narrative": e.narrative, "speaker": e.speaker,
             "choice": e.choice, "choice_label": e.choice_label, "pending": e.pending}
            for e in st.events.log[-30:]
        ],
        "months": [
            {"month": mo.month, "revenue": round(mo.revenue_cents / 100),
             "cost": round(mo.cost_cents / 100), "profit": round(mo.profit_cents / 100),
             "availability": round(mo.availability * 100, 2), "p95": mo.p95_ms,
             "grade": mo.grade, "penalty": round(mo.sla_penalty_cents / 100)}
            for mo in st.months
        ],
        "history": [
            {"week": h.week, "p95": h.p95_ms, "io": round(h.io_util * 100, 1),
             "avail": round((1 - h.error_rate) * 100, 2), "cash": round(h.cash_cents / 100),
             "events": h.event_count}
            for h in st.history[-24:]
        ],
        "debt": [
            {"id": d.id, "severity": round(d.severity, 2), "resolved": d.resolved,
             "category": d.category, "name": d.name or d.id}
            for d in st.debt.items.values()
        ],
        "debt_pressure": round(debt_rules.pressure(st) * 100, 1),
        "debt_by_category": debt_rules.breakdown(st),
        "team": {
            "trust": round(st.org.manager_trust, 1),
            "tickets": st.org.tickets,
            "satisfaction": {k: round(v, 1) for k, v in st.org.satisfaction.items()},
            "monitoring": st.actions.monitoring,
            "docs": st.actions.docs,
        },
        "ending": {
            "stage": st.ending.stage,
            "score": round(st.ending.last_score, 1),
            "breakdown": st.ending.last_breakdown,
            "wave": round(st.ending.wave_pressure, 2),
            "rescue_goal": st.ending.rescue_goal,
            "rescue_target": st.ending.rescue_target,
            "rescue_deadline": st.ending.rescue_deadline,
            "ended": st.ending.ended,
            "ended_week": st.ending.ended_week,
            "flavor": st.ending.flavor,
            "timeline": st.ending.timeline[-8:],
        },
        "tech": _tech_snapshot(st),
        "traits": tech_rules.trait_totals(st),
        "tech_mods": {
            k: round(float(v), 3)
            for k, v in tech_rules.mods(st).items()
            if k != "per_query" and isinstance(v, (int, float))
        },
        "knowledge": tech_rules.knowledge_rows(st),
        "assets": {
            "indexes": [
                {"name": i.name, "table": i.table, "columns": i.columns,
                 "effective_for": i.effective_for, "week": i.week}
                for i in st.assets.indexes
            ],
            "facilities": dict(st.assets.facilities),
            "rewritten_queries": list(st.assets.rewritten_queries),
            "fixes": list(st.assets.fixes),
            "docs": st.assets.docs,
        },
        "career": {
            "resolved_events": list(st.career_rec.resolved_events[-12:]),
            "incidents": list(st.career_rec.incidents[-12:]),
            "projects": list(st.career_rec.projects[-12:]),
        },
        "major_events": event_rules.major_for_client(st),
        "slow_queries": workbench_rules.slow_queries(st),
        "cases": workbench_rules.cases_for(st),
        # 工作台成本 / 风险：让 UI 在玩家动手前就把代价摊开
        "deploy_costs": {k: action_rules.deploy_cost(k)
                         for k in ("index", "rewrite", "fix")},
        "lab": {
            "experiments": len(st.lab.experiments),
            "deployments": len(st.lab.deployments),
            "changes_this_week": workbench_rules.changes_this_week(st),
            "incidents": len(st.career_rec.incidents),
        },
        "decisions": event_rules.pending_for_client(st),
        "blocked": blocked,
        "pending": [{"action": p.action_id, "due": p.due_week} for p in st.actions.pending],
        "skills": {k: round(v, 1) for k, v in st.player.skills.items()},
        "skill_level": round(career_skill(st) * 100, 1),
    }


def load_or_new() -> "object":
    st = saves.load_game(CURRENT_SLOT)
    if st is None:
        st = new_game(seed=20261118)
        saves.save_game(CURRENT_SLOT, st, "自动存档")
    # 顺手把本局新读到的知识 / 科技 / 成就并进跨局档案（无变化不写库）
    saves.sync_profile(st)
    return st


def create_app() -> Flask:
    app = Flask(__name__, template_folder="templates", static_folder="static")

    @app.get("/")
    def index():  # noqa: ANN202
        return render_template("index.html")

    @app.get("/api/state")
    def api_state():  # noqa: ANN202
        return jsonify({"ok": True, "data": snapshot(load_or_new())})

    @app.post("/api/advance")
    def api_advance():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        weeks = max(1, min(8, int(payload.get("weeks", 1))))
        st = load_or_new()
        for _ in range(weeks):
            step(st)
        saves.save_game(CURRENT_SLOT, st, "自动存档")
        return jsonify({"ok": True, "data": snapshot(st)})

    @app.post("/api/action")
    def api_action():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        action_id = payload.get("id", "")
        st = load_or_new()
        if st.meta.sandbox:
            ok, msg = action_rules.start(st, action_id)
        else:
            # 动作前自动打快照，方便回退到"我是在哪一步开始崩的"
            saves.save_snapshot(CURRENT_SLOT, st, f"W{st.meta.week} 执行 {action_id}")
            ok, msg = action_rules.start(st, action_id)
        if ok:
            saves.save_game(CURRENT_SLOT, st, "自动存档")
        return jsonify({"ok": ok, "message": msg, "data": snapshot(st)})

    @app.get("/api/snapshots")
    def api_snapshots():  # noqa: ANN202
        return jsonify({"ok": True, "data": saves.list_snapshots(CURRENT_SLOT)})

    @app.post("/api/rollback")
    def api_rollback():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        st = saves.load_snapshot(int(payload.get("id", 0)))
        if st is None:
            return jsonify({"ok": False, "message": "没有这个快照"}), 404
        saves.save_game(CURRENT_SLOT, st, "回退")
        return jsonify({"ok": True, "data": snapshot(st)})

    @app.post("/api/save")
    def api_save():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        slot = payload.get("slot", CURRENT_SLOT)
        st = load_or_new()
        saves.save_game(slot, st, "手动存档")
        return jsonify({"ok": True, "message": f"已存到 {slot}"})

    @app.post("/api/load")
    def api_load():  # noqa: ANN202
        global CURRENT_SLOT
        payload = request.get_json(silent=True) or {}
        slot = payload.get("slot", CURRENT_SLOT)
        st = saves.load_game(slot)
        if st is None:
            return jsonify({"ok": False, "message": "没有这个存档"}), 404
        CURRENT_SLOT = slot
        return jsonify({"ok": True, "data": snapshot(st)})

    @app.post("/api/new")
    def api_new():  # noqa: ANN202
        global CURRENT_SLOT
        payload = request.get_json(silent=True) or {}
        slot = payload.get("slot", CURRENT_SLOT)
        st = new_game(
            seed=int(payload.get("seed", 20261118)),
            difficulty=payload.get("difficulty", "normal"),
            sandbox=bool(payload.get("sandbox", False)),
            review_week=int(payload.get("review_week", 60)),
        )
        saves.save_game(slot, st, "新游戏")
        CURRENT_SLOT = slot
        return jsonify({"ok": True, "data": snapshot(st)})

    @app.get("/api/slots")
    def api_slots():  # noqa: ANN202
        return jsonify({"ok": True, "data": saves.slot_details()})

    @app.get("/api/export")
    def api_export():  # noqa: ANN202
        from core.model.serde import to_jsonable

        st = load_or_new()
        return jsonify({
            "ok": True,
            "data": {"state": to_jsonable(st), "snapshot": snapshot(st)},
        })

    @app.post("/api/event/choose")
    def api_event_choose():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        st = load_or_new()
        ok, msg = event_rules.resolve_choice(st, payload.get("id", ""), payload.get("choice", ""))
        if ok:
            saves.save_game(CURRENT_SLOT, st, "自动存档")
        return jsonify({"ok": ok, "message": msg, "data": snapshot(st)})

    # ---------------- v1.0：工程工作台 ----------------
    # 没有「读条」「考核」「交卷」「冷却」。玩家随时可以动手，
    # 服务器只如实返回执行计划、物理结果与生产后果。

    @app.post("/api/workbench/inspect")
    def api_wb_inspect():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        st = load_or_new()
        result = workbench_rules.inspect(st, str(payload.get("sql", "")))
        return jsonify({"ok": result.get("ok", False), "data": {**result, "state": snapshot(st)}})

    @app.post("/api/workbench/experiment")
    def api_wb_experiment():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        st = load_or_new()
        result = workbench_rules.experiment(st, str(payload.get("sql", "")))
        if result.get("ok"):
            saves.save_game(CURRENT_SLOT, st, "自动存档")
        return jsonify({"ok": result.get("ok", False), "message": result.get("error", ""),
                        "data": {**result, "state": snapshot(st)}})

    def _wb_deploy(kind: str, label: str, call):  # noqa: ANN202
        """三个部署端点共用的唯一入口。

        1) API 层先做一次统一资源校验（行动力 / 调查精力 / 结束态）；
           规则层 deploy_guard 还会再校验一次 —— 直接调规则函数也绕不过去。
        2) 真正的扣费只在规则层 deploy_finish 里原子完成，API 不碰资源。
        3) **只要状态变了就存档**（包括失败部署的勘察成本、事故代价、
           本周变更流水）。原来只在 ok 时存档，失败一刷新成本就白扣了，
           等于可以无限重试 —— 这正是「重复调用 API 绕过资源扣除」的通道。
        """
        st = load_or_new()
        ok, why = action_rules.check_deploy(st, kind)
        if not ok:
            return jsonify({"ok": False, "message": why, "detail": {"charged": False},
                            "data": snapshot(st)})
        saves.save_snapshot(CURRENT_SLOT, st, f"W{st.meta.week} {label}")
        result = call(st)
        if result.get("charged") or result.get("ok"):
            saves.save_game(CURRENT_SLOT, st, "自动存档")
        return jsonify({
            "ok": result.get("ok", False),
            "message": result.get("message", ""),
            "detail": result,
            "cost": result.get("cost"),
            "risk": result.get("risk"),
            "incident": result.get("incident", False),
            "data": snapshot(st),
        })

    @app.post("/api/workbench/deploy-index")
    def api_wb_deploy_index():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        sql = str(payload.get("sql", ""))
        return _wb_deploy("index", "部署索引",
                          lambda st: workbench_rules.deploy_index(st, sql))

    @app.post("/api/workbench/deploy-rewrite")
    def api_wb_deploy_rewrite():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        query = str(payload.get("query", ""))
        return _wb_deploy("rewrite", "改写查询",
                          lambda st: workbench_rules.deploy_rewrite(st, query))

    @app.post("/api/workbench/deploy-fix")
    def api_wb_deploy_fix():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        case_id = str(payload.get("case_id", ""))
        code = str(payload.get("code", ""))
        return _wb_deploy("fix", f"修复 {case_id}",
                          lambda st: workbench_rules.deploy_fix(st, case_id, code))

    @app.post("/api/workbench/reset-lab")
    def api_wb_reset_lab():  # noqa: ANN202
        workbench_rules.reset_lab()
        return jsonify({"ok": True, "message": "实验库已重置，可以重新试一遍。"})

    @app.post("/api/workbench/read-card")
    def api_wb_read_card():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        st = load_or_new()
        workbench_rules.read_card(st, str(payload.get("id", "")))
        saves.save_game(CURRENT_SLOT, st, "自动存档")
        return jsonify({"ok": True, "data": snapshot(st)})

    @app.post("/api/events/consult")
    def api_events_consult():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        st = load_or_new()
        result = event_rules.consult(st, str(payload.get("id", "")))
        if result.get("ok"):
            saves.save_game(CURRENT_SLOT, st, "自动存档")
        return jsonify({"ok": result.get("ok", False), "message": result.get("message", ""),
                        "data": snapshot(st)})

    # ---------------- v0.9：主界面 / 跨局档案 / 阶段性述职 ----------------

    @app.get("/api/menu")
    def api_menu():  # noqa: ANN202
        kd = tech_rules.knowledge_def()
        prof = saves.get_profile()
        read = set(prof.get("player_knowledge") or [])
        items = []
        for kid, it in kd["items"].items():
            # 知识库是文档，不是奖励：全部条目都完整可读，跨局只记「看过没有」。
            items.append({
                "id": kid, "title": it["title"], "field": it.get("field", ""),
                "field_label": kd["fields"].get(it.get("field", ""), ""),
                "branch": it.get("branch", ""), "read": kid in read,
                "summary": it.get("summary", ""),
                "body": it.get("body", ""), "in_game": it.get("in_game", ""),
                "tell": it.get("tell", ""),
            })
        items.sort(key=lambda r: (r["branch"], r["title"]))
        diffs = [
            {"id": k, "label": v["label"], "desc": v["desc"]}
            for k, v in difficulty_rules.CONFIGS.items()
        ]
        return jsonify({"ok": True, "data": {
            "profile": prof,
            "slots": saves.slot_details(),
            "knowledge": items,
            "knowledge_total": len(items),
            "knowledge_read": len(read),
            "difficulties": diffs,
            "current_slot": CURRENT_SLOT,
            "review_week_range": [50, 100],
        }})

    @app.get("/api/profile")
    def api_profile_get():  # noqa: ANN202
        return jsonify({"ok": True, "data": saves.get_profile()})

    @app.post("/api/profile")
    def api_profile_post():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        data = payload.get("profile", payload)
        return jsonify({"ok": True, "data": saves.set_profile(data)})

    @app.post("/api/slot/delete")
    def api_slot_delete():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        slot = payload.get("slot", "")
        if not slot or slot == CURRENT_SLOT:
            return jsonify({"ok": False, "message": "不能删除正在使用的存档"}), 400
        saves.delete_slot(slot)
        return jsonify({"ok": True, "message": f"已删除 {slot}"})

    @app.post("/api/slot/rename")
    def api_slot_rename():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        slot = payload.get("slot", "")
        if not slot:
            return jsonify({"ok": False, "message": "没有指定存档"}), 400
        saves.rename_slot(slot, str(payload.get("name", "手动存档"))[:40])
        return jsonify({"ok": True, "message": "已重命名"})

    @app.get("/api/review")
    def api_review_get():  # noqa: ANN202
        st = load_or_new()
        return jsonify({"ok": True, "data": {
            "pending": bool(st.review.pending),
            "continued": bool(st.review.continued),
            "review_week": int(st.meta.review_week),
            "week": st.meta.week,
            "report": st.review.last,
            "reports": st.review.reports,
            "milestones": list(st.review.milestones),
            "ended": st.ending.ended,
        }})

    @app.post("/api/review/continue")
    def api_review_continue():  # noqa: ANN202
        st = load_or_new()
        st.review.pending = False
        st.review.continued = True
        st.meta.review_continued = True
        saves.save_game(CURRENT_SLOT, st, "自动存档")
        return jsonify({"ok": True, "message": "继续上班。评价只是参考，日子还得过。",
                        "data": snapshot(st)})

    @app.post("/api/review/config")
    def api_review_config():  # noqa: ANN202
        payload = request.get_json(silent=True) or {}
        st = load_or_new()
        week = max(50, min(100, int(payload.get("review_week", 60))))
        st.meta.review_week = week
        saves.save_game(CURRENT_SLOT, st, "自动存档")
        return jsonify({"ok": True, "message": f"自动评价周改成 W{week}",
                        "data": snapshot(st)})

    return app


app = create_app()
