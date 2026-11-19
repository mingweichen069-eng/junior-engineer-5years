"""工程工作台：调查 → 实验 → 部署 → 物理后果。

V1.0 的核心：知识不是奖励，是工具。
玩家在任何时候都可以直接建索引、改查询、修代码，不需要先证明自己会。
系统不评分、不解锁、不冷却，只按物理事实计算结果：

- 一个索引只对「首列命中该查询谓词 / 排序」的查询有用；
- 对 DATE(created_at) 这种不可谓词化条件，建 created_at 索引也没用，
  除非玩家先把查询改写成区间条件；
- 不管索引有没有用，写放大、磁盘占用、碎片都会真实上涨。

实验库是真实 SQLite（生产 schema 的轻量副本），EXPLAIN QUERY PLAN 是真的。
判题逻辑（core/rules/exam.py）在这里只作为「回归用例」使用：
玩家的修复跑通了生产用例就部署，跑不通就继续查——这不是考试。
v1.1 阶段二起，回归用例分公开 / 隐藏两套，后端一起跑，前端只看得到公开样例。
v1.1.2 阶段三起，查询改写必须提交玩家自己写的 SQL：原始 / 改写两份查询都在
实验库真跑一遍，结果一致、计划里真的用上索引，才算一次有效的生产变更。
"""

from __future__ import annotations

import json
import random
import re
import sqlite3
from pathlib import Path

from core.model.state import DeployedIndex, GameState, Index
from core.rules import actions as action_rules
from core.rules import career as career_rules
from core.rules import tech as tech_rules
from lab import replica, sandbox

ROOT = Path(__file__).resolve().parents[2]
CONTENT = ROOT / "content"
LAB_DB = ROOT / "lab" / "workbench.db"

MOD_KEYS = ("api_overhead", "pool_capacity", "io_wait", "query_time", "contagion", "replica_lag")

# 部署动作 -> 技术档案条目。档案只记录「做过什么」，不授予任何资格。
ASSET_NODE = {
    "index": "db_t1",
    "rewrite": "db_t2",
    "replica": "db_replica",
    "breaker": "be_api",
    "cache": "be_cache",
    "fix": "be_api",
}

# ---------------------------------------------------------------- 生产变更成本 / 风险
# 一次「部署到生产」不是点一下按钮：它要占本周的行动力、调查精力，
# 占一个发布窗口，并且有出事故的可能。数值都复用 core/rules/actions.py
# 的 DEPLOY_COST / check_deploy / charge_deploy，工作台不另立一套资源。
#
# 风险是确定性的（seed + week + kind + key + 第几次变更），不是全局随机数：
# 同一个存档、同一周、同样的操作，结果可复现，也方便写回归测试。
# 基础风险 + 未经实验的额外风险 + 本周连续变更的额外风险。
RISK_BASE = {"index": 0.10, "rewrite": 0.12, "fix": 0.10}
RISK_UNVERIFIED = 0.30       # 没在实验库验证过就直接上线
RISK_CONSECUTIVE = 0.08      # 本周每多一次变更，没有观察期
RISK_CAP = 0.85

# v1.1.2 阶段六：角色技能改称「工程经验」。它不是等级、不 gate 任何操作，
# 只通过三处真实影响模拟：调查所需精力、变更事故风险、职业绩效评价。
# 经验只从真实动手（实验 / 成功上线 / 失败发布复盘 / 事件）沉淀，不刷动作。
EXPERIENCE_RISK_RELIEF = 0.35    # 满经验最多把基础事故风险降 35%
EXPERIENCE_ENERGY_RELIEF = 0.40  # 满经验调查消耗降 40%
EXPERIENCE_BY_KIND = {
    "index": ("db", "sql"),
    "sql": ("sql", "db"),
    "rewrite": ("sql",),
    "fix": ("backend", "sql"),
}


def _experience_factor(state: GameState) -> float:
    """经验降低操作失误概率：只影响风险与消耗，不构成任何门槛。"""
    return 1.0 - EXPERIENCE_RISK_RELIEF * career_rules.skill_level(state)


def _survey_energy(state: GameState, base: float) -> float:
    """调查所需时间：经验越丰富，查同一个问题花的调查精力越少。"""
    return round(base * (1.0 - EXPERIENCE_ENERGY_RELIEF * career_rules.skill_level(state)), 2)


def _gain_experience(state: GameState, kind: str, *, ok: bool = True,
                     amount: float | None = None) -> None:
    """真实动手才沉淀经验。失败发布也有一点排障经验，但沙盘不计入履历。"""
    if state.meta.sandbox:
        return
    gain = float(amount if amount is not None else (0.8 if ok else 0.2))
    for name in EXPERIENCE_BY_KIND.get(kind, ()):
        state.player.skills[name] = min(100.0, state.player.skills.get(name, 0.0) + gain)

# 变更窗口冲击（复用 evaluate.py 已有的 lock_week / lock_ms 锁尖峰机制，
# 不新造一套指标）。部署当周写入「下一周」的窗口，player 推进一周就能
# 在 p95 / 错误率上看到这次变更的真实代价。
CHANGE_WINDOW_MS = {"index": 260.0, "rewrite": 180.0, "fix": 150.0}
INCIDENT_MS = 900.0           # 出事故时的 p95 尖峰
INCIDENT_TRUST = 4.0          # 主管信任损失
INCIDENT_TICKETS = 6          # 涌入的工单
INCIDENT_CASH_CENTS = 30000   # 一次 SLA 赔偿 / 回滚成本


def _signature(kind: str, sql_or_id: str, parsed=None) -> str:
    """给「这次做了什么」一个稳定签名，用来关联实验记录、去重和事故判定。"""
    if kind == "index" and parsed:
        _, table, columns = parsed
        return f"index:{table}:{','.join(c.lower() for c in columns)}"
    if kind == "index":
        return "index:" + re.sub(r"\s+", " ", (sql_or_id or "").strip().lower())
    if kind == "sql":
        return "sql:" + re.sub(r"\s+", " ", (sql_or_id or "").strip().lower())
    return f"{kind}:{sql_or_id}"


def record_experiment(state: GameState, kind: str, key: str, ok: bool, note: str = "") -> None:
    """在实验库里动过手 → 记一笔实验。它只是个人经验，不会变成公司资产。"""
    state.lab.experiments.append({
        "week": state.meta.week, "kind": kind, "key": key,
        "ok": bool(ok), "note": note[:200],
    })
    if len(state.lab.experiments) > 120:
        del state.lab.experiments[:-120]


def has_experiment(state: GameState, kind: str, key: str) -> bool:
    """这次部署的方案，玩家在实验库里验证过吗？"""
    return any(
        e.get("kind") == kind and e.get("key") == key and e.get("ok")
        for e in state.lab.experiments
    )


def changes_this_week(state: GameState) -> int:
    return sum(1 for d in state.lab.deployments if d.get("week") == state.meta.week)


def risk_of(state: GameState, kind: str, key: str) -> dict:
    """只读：算这次变更的事故概率，并把构成解释清楚。

    工程经验只折减「基础失误率」；没实验过 / 本周连着上线这类客观风险不打折。
    """
    verified = has_experiment(state, kind, key)
    untested = 0.0 if verified else RISK_UNVERIFIED
    consecutive = min(0.32, changes_this_week(state) * RISK_CONSECUTIVE)
    factor = _experience_factor(state)
    base = RISK_BASE.get(kind, 0.12) * factor
    total = min(RISK_CAP, base + untested + consecutive)
    return {
        "base": round(base, 3),
        "untested": round(untested, 3),
        "consecutive": round(consecutive, 3),
        "total": round(total, 3),
        "verified": verified,
        "changes_this_week": changes_this_week(state),
        "experience_factor": round(factor, 3),
    }


def _roll_incident(state: GameState, kind: str, key: str, risk: dict) -> bool:
    """确定性掷骰：同一存档同一周同一操作结果固定，便于复现与测试。"""
    if state.meta.sandbox:
        return False
    nonce = f"{state.meta.seed}:{state.meta.week}:{kind}:{key}:{len(state.lab.deployments)}"
    return random.Random(nonce).random() < risk["total"]


def _change_window(state: GameState, kind: str, incident: bool) -> float:
    """生产变更占用一个发布窗口：下一周的 p95 会如实抬高。

    普通上线只是一次温和的抖动（change_week / change_ms，evaluate 只抬 p95）；
    真出了事故才复用已有的 lock_week / lock_ms 强冲击（p95 + 错误率），
    这样不会因为「点了几下部署」就把整条可用性曲线打歪。
    """
    ms = INCIDENT_MS if incident else CHANGE_WINDOW_MS.get(kind, 200.0)
    if state.meta.sandbox:
        return ms
    due = state.meta.week + 1
    if incident:
        if state.flags.get("lock_week") != due or float(state.flags.get("lock_ms", 0)) < ms:
            state.flags["lock_week"] = due
            state.flags["lock_ms"] = ms
    else:
        if state.flags.get("change_week") != due or float(state.flags.get("change_ms", 0)) < ms:
            state.flags["change_week"] = due
            state.flags["change_ms"] = ms
    return ms


def _apply_incident(state: GameState, kind: str, key: str, risk: dict) -> dict:
    """事故是工程后果，不是考试惩罚：履历污点 + 信任 / 工单 / SLA 赔偿 + 线上抖动。"""
    state.career_rec.incidents.append({
        "week": state.meta.week, "kind": kind, "key": key,
        "risk": risk["total"], "note": "未经充分验证的生产变更引发短时故障，回滚并赔偿",
    })
    state.org.manager_trust = max(0.0, state.org.manager_trust - INCIDENT_TRUST)
    state.org.tickets += INCIDENT_TICKETS
    state.company.cash_cents -= INCIDENT_CASH_CENTS
    mark(state, ASSET_NODE.get(kind, ""), "incident")
    return {
        "week": state.meta.week, "kind": kind, "risk": risk["total"],
        "trust_lost": INCIDENT_TRUST, "tickets": INCIDENT_TICKETS,
        "cash_cents": INCIDENT_CASH_CENTS, "p95_spike_ms": INCIDENT_MS,
    }


def deploy_guard(state: GameState, kind: str, key: str, *, already: bool = False) -> dict:
    """所有部署的唯一入口：统一资源校验 + 去重 + 风险预告。不修改状态。

    Web API、测试夹具、平衡脚本最终都要走这里，绕不过 check_deploy()。
    """
    if state.ending.ended and not state.meta.sandbox:
        return {"ok": False, "allowed": False, "message": "游戏已经结束", "charged": False}
    if already:
        return {"ok": False, "allowed": False, "message": "这项变更已经部署过了",
                "duplicate": True, "charged": False}
    ok, why = action_rules.check_deploy(state, kind)
    if not ok:
        return {"ok": False, "allowed": False, "message": why, "charged": False}
    return {"ok": True, "allowed": True, "kind": kind, "key": key,
            "risk": risk_of(state, kind, key), "cost": action_rules.deploy_cost(kind)}


def deploy_finish(state: GameState, guard: dict, *, success: bool,
                  message: str, extra: dict | None = None) -> dict:
    """统一收口：扣费 → 事故判定 → 变更窗口 → 记部署流水。

    success=False（SQL 写错、回归没过）也扣勘察 / 回滚成本，但不登记资产。
    """
    kind, key, risk = guard["kind"], guard["key"], guard["risk"]
    cost = action_rules.charge_deploy(state, kind, survey_only=not success)
    incident = success and _roll_incident(state, kind, key, risk)
    window_ms = _change_window(state, kind, incident)
    detail = _apply_incident(state, kind, key, risk) if incident else None

    state.lab.deployments.append({
        "week": state.meta.week, "kind": kind, "key": key,
        "ok": bool(success), "verified": risk["verified"], "risk": risk["total"],
        "cost": cost, "incident": bool(incident),
    })
    if len(state.lab.deployments) > 160:
        del state.lab.deployments[:-160]
    _gain_experience(state, kind, ok=bool(success))

    out = {
        "ok": bool(success), "message": message, "charged": True,
        "cost": cost, "risk": risk, "incident": bool(incident),
        "change_window_ms": window_ms,
    }
    if detail:
        out["incident_detail"] = detail
    if extra:
        out.update(extra)
    return out


INDEX_RE = re.compile(
    r"^create\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?"
    r"([a-z_][a-z0-9_]*)\s+on\s+([a-z_][a-z0-9_]*)\s*\(([a-z0-9_,\s]+)\)\s*;?$",
    re.I,
)


def catalog() -> list[dict]:
    return json.loads((CONTENT / "queries.json").read_text(encoding="utf-8"))["queries"]


# 只给平衡脚本 / 测试夹具使用：模拟一个「真的会写 SQL」的玩家。
# 它不通过任何 API 返回给前端，玩家在界面上看到的只有原始查询，没有参考答案。
REFERENCE_REWRITES = {
    "q_dashboard": (
        "SELECT status, COUNT(*) AS cnt, SUM(amount) AS gmv FROM orders "
        "WHERE created_at >= '2023-11-02 00:00:00' "
        "AND created_at < '2023-11-03 00:00:00' "
        "GROUP BY status ORDER BY status"
    ),
}


# v1.1.2 阶段四之前，实验库会预建这几个「生产 schema 里并不存在」的索引，
# 让玩家还没部署就看到 SEARCH ... USING INDEX。老实验库文件需要就地重建。
LEGACY_LAB_INDEXES = {"idx_orders_user_created", "idx_payments_order", "idx_payments_created"}


def ensure_lab() -> Path:
    """实验库：独立轻量副本，玩家随便建索引、随便重置，不污染判题主库。

    规模与初始索引都显式对齐生产 schema：4,000 行、只建 schema.json 里的索引。
    """
    if not LAB_DB.exists() or LAB_DB.stat().st_size < 1000:
        replica.build_lab(LAB_DB, orders_n=replica.LAB_ORDERS)
        return LAB_DB
    # 只清掉旧版预置的多余索引；玩家自己在实验库里建的索引要保留。
    con = sqlite3.connect(LAB_DB)
    try:
        have = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            )
        }
    finally:
        con.close()
    if have & LEGACY_LAB_INDEXES:
        replica.build_lab(LAB_DB, orders_n=replica.LAB_ORDERS)
    return LAB_DB


def reset_lab() -> dict:
    """只重置实验库，绝不碰公司已经部署到生产的资产。

    实验库是玩家随手试的地方；生产资产是另一套状态（CompanyAssets），
    重置实验库不能让已经上线的索引 / 改写 / 修复凭空消失。
    """
    # 在同一个库文件里重建表，不删文件（删文件会被锁 / 被安全策略拦）
    replica.build_lab(LAB_DB, orders_n=replica.LAB_ORDERS)
    return {"ok": True, "message": "实验库已重置：表和初始索引恢复原样，你自己建的索引都清掉了；"
                                   "已经部署到生产的资产不受影响。"}


# ---------------------------------------------------------------- 档案


def mark(state: GameState, node_id: str, flag: str) -> None:
    """给技术档案记一笔。纯粹是记录，不改变任何可用操作。"""
    if not node_id:
        return
    e = state.tech.entries.setdefault(
        node_id, {"seen": False, "experimented": False, "deployed": False, "incident": False, "weeks": []}
    )
    e[flag] = True
    if state.meta.week not in e["weeks"]:
        e["weeks"].append(state.meta.week)


def read_card(state: GameState, card_id: str) -> None:
    """查阅知识卡：内容本来就全部开放，这里只留一个「看过」的足迹。"""
    state.tech.knowledge[card_id] = True


# ---------------------------------------------------------------- 调查


def is_rewritten(state: GameState, qid: str) -> bool:
    """这条查询在生产上是否真的被改写成可索引形式。

    只读公司资产：v1.1.2 起 ``rewrite_verified`` 由实验库真实执行计划写入；
    旧存档与行动面板的老路径没有这个字典，回退到 ``rewritten_queries``，
    保证已有存档 / 既有经营动作的效果不倒退。绝不读玩家知识或技能。
    """
    verified = getattr(state.assets, "rewrite_verified", None) or {}
    if qid in verified:
        return bool(verified[qid])
    return qid in state.assets.rewritten_queries


def query_columns(q: dict, state: GameState) -> list[str]:
    """这条查询在物理上能被哪些列上的索引帮到。

    non_sargable（函数包住索引列）在查询被改写前不算，这正是「建了索引却没改善」的根因。
    """
    cols: list[str] = []
    for p in q.get("predicates", []):
        if p.get("op") == "non_sargable":
            if is_rewritten(state, q["id"]):
                cols.append(p["column"])
            continue
        cols.append(p["column"])
    cols.extend(q.get("order_by", []))
    return list(dict.fromkeys(cols))


def effective_for(state: GameState, table: str, columns: list[str]) -> list[str]:
    """物理判定：这个索引对哪些查询真的有用。不查任何「技能」或「解锁」。

    索引首列必须命中 WHERE 谓词；只命中 ORDER BY 不算（除非复合索引里
    还带了谓词列）。这就是「照抄一条 CREATE INDEX 建成了，查询却没变快」的原因。
    """
    if not columns:
        return []
    out: list[str] = []
    for q in catalog():
        if q["table"] != table or q.get("kind") in ("write", "other"):
            continue
        if not q.get("active"):
            continue
        preds = [
            p["column"] for p in q.get("predicates", [])
            if p.get("op") != "non_sargable" or is_rewritten(state, q["id"])
        ]
        order = list(q.get("order_by", []))
        hit = columns[0] in preds
        if not hit and columns[0] in order:
            hit = any(c in preds for c in columns[1:])
        if hit:
            out.append(q["id"])
    return out


def slow_queries(state: GameState) -> list[dict]:
    """监控台：列出活跃查询、它的谓词、以及当前有没有索引真正帮到它。"""
    rows = []
    for q in catalog():
        if not q.get("active") or q.get("kind") in ("write", "other"):
            continue
        needed = query_columns(q, state)
        helping = [
            i.name or ",".join(i.columns)
            for i in state.assets.indexes
            if i.table == q["table"] and q["id"] in i.effective_for
        ]
        preds = ", ".join(
            f"{p['column']}({p.get('op', 'eq')})" for p in q.get("predicates", [])
        ) or "无"
        rows.append({
            "id": q["id"],
            "name": q["name"],
            "table": q["table"],
            "predicates": preds,
            "order_by": ", ".join(q.get("order_by", [])) or "无",
            "indexable_columns": needed,
            "helping_indexes": helping,
            "rewritten": q["id"] in state.assets.rewritten_queries,
            "hint": (
                "谓词列被函数包住了，目前建索引也用不上"
                if not needed and q.get("predicates")
                else ("已有索引在起作用" if helping else "没有任何索引帮到这条查询")
            ),
        })
    return rows


def inspect(state: GameState, sql: str) -> dict:
    """看一条 SQL 的真实执行计划（只读，实验库）。"""
    r = sandbox.run(sql, path=ensure_lab(), prepared=True)
    return {
        "ok": r.get("ok", False),
        "error": r.get("error", ""),
        "plan": r.get("plan", []),
        "used_index": r.get("used_index", False),
        "warnings": r.get("warnings", []),
        "rows": r.get("rows", []),
        "row_count": r.get("row_count", 0),
        "elapsed_ms": r.get("elapsed_ms", 0),
    }


# ---------------------------------------------------------------- 查询改写：真执行 + 结果一致性


def rewrite_queries(state: GameState) -> list[dict]:
    """前端「查询改写」区：只给原始 SQL 与已部署资产，绝不给参考答案。"""
    sqls = getattr(state.assets, "rewrite_sql", None) or {}
    out = []
    for q in catalog():
        if not q.get("rewriteable"):
            continue
        qid = q["id"]
        out.append({
            "id": qid,
            "name": q["name"],
            "original_sql": q.get("sql", ""),
            "index_column": q.get("index_column", ""),
            "deployed_sql": sqls.get(qid, ""),
            "verified": bool((getattr(state.assets, "rewrite_verified", None) or {}).get(qid)),
            "effective": is_rewritten(state, qid),
        })
    return out


def _sql_cell(v):
    """SQLite 返回值归一化：1 与 1.0 不该被当成结果不一致。"""
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _rows_signature(r: dict) -> list[tuple]:
    return sorted((tuple(_sql_cell(v) for v in row) for row in r.get("rows", [])), key=repr)


def _same_result(a: dict, b: dict) -> bool:
    """结果一致性：列数相同 + 行多重集合相同；行序与列别名不同不算错。

    被 MAX_ROWS 截断的结果无法判定一致，直接判不一致——不能拿半张表骗过校验。
    """
    if not a.get("ok") or not b.get("ok"):
        return False
    if a.get("truncated") or b.get("truncated"):
        return False
    if len(a.get("columns") or []) != len(b.get("columns") or []):
        return False
    return _rows_signature(a) == _rows_signature(b)


def _sql_view(r: dict) -> dict:
    return {
        "ok": bool(r.get("ok")),
        "error": r.get("error", ""),
        "plan": r.get("plan", []),
        "used_index": bool(r.get("used_index")),
        "warnings": r.get("warnings", []),
        "row_count": r.get("row_count", 0),
        "columns": r.get("columns", []),
        "truncated": bool(r.get("truncated")),
        # 只是本机实验库的耗时，不是生产性能指标
        "lab_elapsed_ms": r.get("elapsed_ms", 0),
    }


def check_rewrite(state: GameState, query_id: str, sql: str) -> dict:
    """把「原始查询」与「玩家提交的改写」都在实验库里真跑一遍。

    不修改游戏状态。硬性失败：SQL 跑不起来 / 结果不一致 / 结果被截断。
    软性结果 verified：执行计划里是否真的用上了目标列的索引。
    两者都真执行，不是比对字符串，也不是任何预设答案。
    """
    q = next((x for x in catalog() if x["id"] == query_id), None)
    text = str(sql or "").strip()
    out: dict = {
        "query_id": query_id, "ok": False, "verified": False, "message": "",
        "original_sql": "", "submitted_sql": text,
        "original": {}, "submitted": {}, "rows_equal": False,
    }
    if not q or not q.get("rewriteable") or not q.get("sql"):
        out["message"] = "这条查询不开放改写，或没有配置原始 SQL"
        return out
    out["original_sql"] = q["sql"]
    if not text:
        out["message"] = "还没有提交改写后的 SQL：请把你自己写的查询贴进来"
        return out

    path = ensure_lab()
    orig = sandbox.run(q["sql"], path=path, prepared=True)
    sub = sandbox.run(text, path=path, prepared=True)
    out["original"], out["submitted"] = _sql_view(orig), _sql_view(sub)
    if not orig.get("ok"):
        out["message"] = "基准查询在实验库里跑不起来（这是配置问题，不是你的 SQL 错了）"
        return out
    if not sub.get("ok"):
        out["message"] = "你提交的 SQL 跑不起来：" + str(sub.get("error") or "未知错误")
        return out
    out["rows_equal"] = _same_result(orig, sub)
    if not out["rows_equal"]:
        out["message"] = (
            "两份查询返回的结果不一致（列数、行或是否被截断不同）："
            "改写不能改变业务含义，这次不会部署。"
        )
        return out
    col = str(q.get("index_column") or "").lower()
    plan_text = " ".join(str(p.get("detail", "")) for p in (sub.get("plan") or [])).lower()
    out["verified"] = bool(sub.get("used_index")) and (not col or f"({col}" in plan_text)
    out["ok"] = True
    out["message"] = (
        f"结果一致，且执行计划真的用上了 {col} 索引：这次改写能改善生产查询。"
        if out["verified"] else
        "结果一致，但执行计划里没有用上目标索引：这条改写不会带来性能收益，"
        "部署了也只是换了个写法。"
    )
    return out


def check_rewrite_for_client(state: GameState, query_id: str, sql: str) -> dict:
    """「对比验证」按钮：只在实验库里跑，不部署。花一点调查精力并记一笔实验。"""
    r = check_rewrite(state, query_id, sql)
    key = _signature("rewrite", str(sql or ""))
    record_experiment(state, "rewrite", key, bool(r.get("ok")),
                      note="改写对比：" + (r.get("message") or "")[:120])
    state.player.energy = max(0.0, state.player.energy - _survey_energy(state, 3.0))
    if r.get("ok"):
        _gain_experience(state, "rewrite", amount=0.3)
    r["energy"] = round(state.player.energy, 1)
    r["experiment_recorded"] = True
    return r


def experiment(state: GameState, sql: str) -> dict:
    """在实验库里动手：SELECT 看计划，CREATE INDEX 真的建一个再让你查。

    这里没有「对错」，只有物理结果。建错了索引，执行计划会如实告诉你没用上。
    成功的实验会记进 LabState.experiments —— 它只表示「玩家验证过这个方案」，
    能降低随后同方案部署的事故概率，但绝不会自动变成公司资产。
    """
    r = sandbox.run(sql, path=ensure_lab(), allow_scan=True, prepared=True)
    parsed = parse_index(sql)
    kind = "index" if parsed else "sql"
    key = _signature(kind, sql, parsed)
    record_experiment(state, kind, key, bool(r.get("ok")),
                      note="实验库执行 " + (r.get("error") or "成功")[:120])
    mark(state, ASSET_NODE["index"], "experimented")
    state.player.energy = max(0.0, state.player.energy - _survey_energy(state, 4.0))
    if r.get("ok"):
        _gain_experience(state, kind, amount=0.3)
    return {
        "ok": r.get("ok", False),
        "error": r.get("error", ""),
        "plan": r.get("plan", []),
        "used_index": r.get("used_index", False),
        "warnings": r.get("warnings", []),
        "rows": r.get("rows", []),
        "row_count": r.get("row_count", 0),
        "elapsed_ms": r.get("elapsed_ms", 0),
        "energy": round(state.player.energy, 1),
        "experiment_recorded": bool(r.get("ok")),
        "experiment_key": key,
    }


def parse_index(sql: str) -> tuple[str, str, list[str]] | None:
    m = INDEX_RE.match((sql or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2), [c.strip() for c in m.group(3).split(",") if c.strip()]


# ---------------------------------------------------------------- 部署


def deploy_index(state: GameState, sql: str) -> dict:
    """把 CREATE INDEX 部署到生产。

    不管玩家懂不懂原理都允许部署：没有「必须先实验」的门槛，但没实验过
    风险更高。系统只按列是否命中查询算收益，写放大 / 磁盘上涨永远真实。
    """
    parsed = parse_index(sql)
    key = _signature("index", sql, parsed)
    # 同名或同列组合都算重复：不重复扣费、不重复登记、不重复承担风险。
    already = False
    if parsed:
        _, table, columns = parsed
        already = any(
            i.name == parsed[0]
            or (i.table == table and [c.lower() for c in i.columns] == [c.lower() for c in columns])
            for i in state.assets.indexes
        )
    guard = deploy_guard(state, "index", key, already=already)
    if not guard["allowed"]:
        return guard
    if not parsed:
        return deploy_finish(state, guard, success=False,
                             message="只支持 CREATE INDEX 语句。这次无效上线仍按勘察 / 回滚计入成本。")
    name, table, columns = parsed
    if table not in state.db.tables:
        return deploy_finish(state, guard, success=False,
                             message=f"没有表 {table}。这次无效上线仍按勘察 / 回滚计入成本。")

    eff = effective_for(state, table, columns)
    di = DeployedIndex(table=table, columns=columns, name=name, effective_for=eff, week=state.meta.week)
    state.assets.indexes.append(di)
    # 同步进模拟层的表结构：磁盘占用 / 写放大 / 碎片由 evaluate 自然算出
    state.db.tables[table].indexes[name] = Index(columns=columns)
    mark(state, ASSET_NODE["index"], "deployed")
    state.career_rec.projects.append(f"W{state.meta.week} 部署索引 {name}({','.join(columns)})")

    if eff:
        names = ", ".join(next((q["name"] for q in catalog() if q["id"] == x), x) for x in eff)
        msg = f"已部署 {name}。物理上帮到：{names}。写放大与磁盘占用同时上升。"
    else:
        msg = (
            f"已部署 {name}，但它没有帮到任何线上查询。"
            "写入变慢、磁盘占用上升了——去执行计划里看看为什么。"
        )
    return deploy_finish(state, guard, success=True, message=msg,
                         extra={"effective_for": eff, "columns": columns, "table": table})


def deploy_rewrite(state: GameState, query_id: str, sql: str = "") -> dict:
    """把**玩家自己写的**改写 SQL 部署到生产。

    v1.1.2 阶段三：不再只收一个 query_id，也不再有点一下就完事的预设按钮。
    1) 原始查询与玩家 SQL 都在实验库里真跑（check_rewrite）；
    2) 结果不一致 / 跑不起来 = 失败的发布，只收勘察 / 回滚成本；
    3) 结果一致但没用上索引 = 允许部署，SQL 存进资产，但没有任何性能收益；
    4) 结果一致且计划里用上目标索引 = 有效资产，索引的物理收益随即重算。

    生产模拟读的是 rewrite_sql / rewrite_verified / rewritten_queries 这套真实
    资产，而不是一个凭空置位的 rewritten=True 标志。
    """
    q = next((x for x in catalog() if x["id"] == query_id), None)
    key = _signature("rewrite", str(sql or ""))
    guard = deploy_guard(state, "rewrite", key,
                         already=query_id in state.assets.rewritten_queries)
    if not guard["allowed"]:
        return guard
    if not q or not q.get("rewriteable"):
        return deploy_finish(state, guard, success=False,
                             message="没有这条可改写的查询。这次无效上线仍按勘察 / 回滚计入成本。")

    check = check_rewrite(state, query_id, sql)
    if not check["ok"]:
        return deploy_finish(
            state, guard, success=False,
            message=f"改写没通过生产校验：{check['message']} 这次失败上线按勘察 / 回滚计入成本。",
            extra={"rewrite_check": check},
        )

    # 到这里两份查询真的跑过、结果真的相同：把玩家提交的 SQL 存成公司资产。
    state.assets.rewrite_sql[query_id] = check["submitted_sql"]
    verified = bool(check["verified"])
    state.assets.rewrite_verified[query_id] = verified
    mark(state, ASSET_NODE["rewrite"], "deployed")
    state.career_rec.projects.append(
        f"W{state.meta.week} 改写查询 {q['name']}（提交 {len(check['submitted_sql'])} 字符 SQL）"
    )

    if verified:
        if query_id not in state.assets.rewritten_queries:
            state.assets.rewritten_queries.append(query_id)
        state.flags[f"{query_id}_rewritten"] = True
        # 已经部署的索引要按新谓词重新判定：这一步才让 per_query 收益真的发生
        for di in state.assets.indexes:
            di.effective_for = effective_for(state, di.table, di.columns)
        msg = (f"已部署你写的「{q['name']}」改写：实验库里两份结果完全一致，"
               "执行计划从全表扫描变成走索引，相关查询的生产耗时随之下降。")
    else:
        msg = (f"你写的「{q['name']}」SQL 已部署到生产，但执行计划没用上目标索引："
               "结果没变，性能也不会变。资产里留着这份 SQL，想改善就回去重写。")
    return deploy_finish(state, guard, success=True, message=msg,
                         extra={"rewrite_check": check, "verified": verified})


def deploy_fix(state: GameState, case_id: str, code: str) -> dict:
    """代码修复的生产回归：跑通用例就部署，跑不通就继续查。

    没有分数、没有冷却、没有重考——只有「这个修复在生产用例上能不能跑」。
    回归跑挂也算一次失败的发布，同样要付勘察 / 回滚成本。
    """
    from core.rules import exam as exam_engine

    tasks = exam_engine.load()["tasks"]
    task = tasks.get(case_id)
    key = _signature("fix", case_id)
    guard = deploy_guard(state, "fix", key, already=case_id in state.assets.fixes)
    if not guard["allowed"]:
        return guard
    if not task:
        return deploy_finish(state, guard, success=False,
                             message="没有这个修复用例。这次无效上线仍按勘察 / 回滚计入成本。")

    # v1.1 阶段二：公开用例 + 隐藏回归用例一起跑，隐藏输入不回传前端。
    # 针对公开样例硬编码的答案会在隐藏用例上挂掉，只能回去改真正的实现。
    ok, msg, detail = exam_engine.validate_regression(task, code)
    mark(state, ASSET_NODE["fix"], "experimented")
    if not ok:
        return deploy_finish(state, guard, success=False, message=msg, extra={"detail": detail})
    state.assets.fixes.append(case_id)
    mark(state, ASSET_NODE["fix"], "deployed")
    state.career_rec.projects.append(f"W{state.meta.week} 修复并部署 {task.get('title', case_id)}")
    return deploy_finish(state, guard, success=True,
                         message="回归用例全部通过，修复已部署到生产。", extra={"detail": detail})


def cases_for(state: GameState) -> list[dict]:
    """事件修复用的回归用例清单（不叫题目、不显示答案）。"""
    from core.rules import exam as exam_engine

    tasks = exam_engine.load()["tasks"]
    out = []
    for k, t in tasks.items():
        if t.get("kind") != "code":
            continue
        out.append({
            "id": k,
            "title": t.get("title", k),
            "brief": t.get("brief", ""),
            "entry": t.get("entry", ""),
            "cases": exam_engine.public_cases(t),   # 只公开；隐藏用例不进 snapshot
            "hidden_count": len(exam_engine.hidden_cases(t)),
            "starter": t.get("starter", ""),
            "deployed": k in state.assets.fixes,
        })
    return out


# ---------------------------------------------------------------- 数值修正


def mods(state: GameState) -> dict[str, float]:
    """模拟层修正：唯一来源是公司资产，不是「角色掌握了什么」。"""
    out: dict[str, float] = {k: 1.0 for k in MOD_KEYS}
    per_query: dict[str, float] = {}

    for di in state.assets.indexes:
        if di.effective_for:
            for qid in di.effective_for:
                per_query[qid] = per_query.get(qid, 1.0) * 0.55
        # 每个二级索引都真实增加写入成本与磁盘开销（与有没有用无关）
        out["io_wait"] *= 1.05

    f = state.assets.facilities
    reps = int(f.get("replica", 0))
    if reps:
        out["query_time"] *= max(0.60, 1.0 - 0.12 * reps)
        out["replica_lag"] *= 1.0 + 0.05 * reps
    if f.get("circuit_breaker"):
        out["contagion"] *= 0.55
    if f.get("cache"):
        out["query_time"] *= 0.88
    if f.get("monitoring"):
        out["api_overhead"] *= 0.97

    for key in MOD_KEYS:
        out[key] = max(0.5, min(2.0, float(out[key])))
    out["per_query"] = per_query          # type: ignore[assignment]
    return out


def archive_rows(state: GameState) -> list[dict]:
    """技术档案视图：六轨道只是分类，节点不再有前置、读条或考核。"""
    data = tech_rules.load()
    rows = []
    for n in data["nodes"]:
        e = state.tech.entries.get(n["id"], {})
        rows.append({
            "id": n["id"],
            "name": n["name"],
            "branch": n["branch"],
            "tier": n.get("tier", 1),
            "desc": n.get("desc", ""),
            "seen": bool(e.get("seen")),
            "experimented": bool(e.get("experimented")),
            "deployed": bool(e.get("deployed")),
            "incident": bool(e.get("incident")),
            "weeks": list(e.get("weeks", [])),
        })
    return rows
