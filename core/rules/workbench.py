"""工程工作台：调查 → 实验 → 部署 → 物理后果。

V1.0 的核心：知识不是奖励，是工具。
玩家在任何时候都可以直接建索引、改查询、修代码，不需要先证明自己会。
系统不评分、不解锁、不冷却，只按物理事实计算结果：

- 一个索引只对「首列命中该查询谓词 / 排序」的查询有用；
- 对 DATE(created_at) 这种不可谓词化条件，建 created_at 索引也没用，
  除非玩家先把查询改写成区间条件；
- 不管索引有没有用，写放大、磁盘占用、碎片都会真实上涨。

实验库是真实 SQLite（20 万行副本的独立拷贝），EXPLAIN QUERY PLAN 是真的。
判题逻辑（core/rules/exam.py）在这里只作为「回归用例」使用：
玩家的修复跑通了生产用例就部署，跑不通就继续查——这不是考试。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from core.model.state import DeployedIndex, GameState, Index
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

INDEX_RE = re.compile(
    r"^create\s+(?:unique\s+)?index\s+(?:if\s+not\s+exists\s+)?"
    r"([a-z_][a-z0-9_]*)\s+on\s+([a-z_][a-z0-9_]*)\s*\(([a-z0-9_,\s]+)\)\s*;?$",
    re.I,
)


def catalog() -> list[dict]:
    return json.loads((CONTENT / "queries.json").read_text(encoding="utf-8"))["queries"]


def ensure_lab() -> Path:
    """实验库：独立轻量副本，玩家随便建索引、随便重置，不污染判题主库。"""
    if not LAB_DB.exists() or LAB_DB.stat().st_size < 1000:
        replica.build_lab(LAB_DB)
    return LAB_DB


def reset_lab() -> dict:
    # 在同一个库文件里重建表，不删文件（删文件会被锁 / 被安全策略拦）
    replica.build_lab(LAB_DB)
    return {"ok": True, "message": "实验库已重置：表和初始索引恢复原样，你自己建的索引都清掉了。"}


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


def query_columns(q: dict, state: GameState) -> list[str]:
    """这条查询在物理上能被哪些列上的索引帮到。

    non_sargable（函数包住索引列）在查询被改写前不算，这正是「建了索引却没改善」的根因。
    """
    cols: list[str] = []
    for p in q.get("predicates", []):
        if p.get("op") == "non_sargable":
            if q["id"] in state.assets.rewritten_queries:
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
            if p.get("op") != "non_sargable" or q["id"] in state.assets.rewritten_queries
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


def experiment(state: GameState, sql: str) -> dict:
    """在实验库里动手：SELECT 看计划，CREATE INDEX 真的建一个再让你查。

    这里没有「对错」，只有物理结果。建错了索引，执行计划会如实告诉你没用上。
    """
    r = sandbox.run(sql, path=ensure_lab(), allow_scan=True, prepared=True)
    mark(state, ASSET_NODE["index"], "experimented")
    state.player.energy = max(0.0, state.player.energy - 4)
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
    }


def parse_index(sql: str) -> tuple[str, str, list[str]] | None:
    m = INDEX_RE.match((sql or "").strip())
    if not m:
        return None
    return m.group(1), m.group(2), [c.strip() for c in m.group(3).split(",") if c.strip()]


# ---------------------------------------------------------------- 部署


def deploy_index(state: GameState, sql: str) -> dict:
    """把实验里写好的 CREATE INDEX 部署到生产。

    不管玩家懂不懂原理，都允许部署；系统只按列是否命中查询来算收益。
    """
    parsed = parse_index(sql)
    if not parsed:
        return {"ok": False, "message": "只支持 CREATE INDEX 语句"}
    name, table, columns = parsed
    if table not in state.db.tables:
        return {"ok": False, "message": f"没有表 {table}"}
    if any(i.name == name for i in state.assets.indexes):
        return {"ok": False, "message": f"索引 {name} 已经部署过了"}

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
    return {"ok": True, "message": msg, "effective_for": eff, "columns": columns, "table": table}


def deploy_rewrite(state: GameState, query_id: str) -> dict:
    """把查询改写成可走索引的形式。改写之后，对应列上的索引才真正生效。"""
    q = next((x for x in catalog() if x["id"] == query_id), None)
    if not q:
        return {"ok": False, "message": "没有这条查询"}
    if query_id in state.assets.rewritten_queries:
        return {"ok": False, "message": "已经改写过了"}
    state.assets.rewritten_queries.append(query_id)
    state.flags[f"{query_id}_rewritten"] = True
    mark(state, ASSET_NODE["rewrite"], "deployed")
    # 已经部署的索引要按新谓词重新判定
    for di in state.assets.indexes:
        di.effective_for = effective_for(state, di.table, di.columns)
    state.career_rec.projects.append(f"W{state.meta.week} 改写查询 {q['name']}")
    return {"ok": True, "message": f"已改写「{q['name']}」为区间条件；相关索引现在可以命中了。"}


def deploy_fix(state: GameState, case_id: str, code: str) -> dict:
    """代码修复的生产回归：跑通用例就部署，跑不通就继续查。

    没有分数、没有冷却、没有重考——只有「这个修复在生产用例上能不能跑」。
    """
    from core.rules import exam as exam_engine

    tasks = exam_engine.load()["tasks"]
    task = tasks.get(case_id)
    if not task:
        return {"ok": False, "message": "没有这个修复用例"}
    ok, msg, detail = exam_engine.validate(task, code)
    mark(state, ASSET_NODE["fix"], "experimented")
    state.player.energy = max(0.0, state.player.energy - 4)
    if not ok:
        return {"ok": False, "message": msg, "detail": detail}
    if case_id not in state.assets.fixes:
        state.assets.fixes.append(case_id)
    mark(state, ASSET_NODE["fix"], "deployed")
    state.career_rec.projects.append(f"W{state.meta.week} 修复并部署 {task.get('title', case_id)}")
    return {"ok": True, "message": "回归用例全部通过，修复已部署到生产。", "detail": detail}


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
            "cases": t.get("public_cases") or t.get("cases", []),
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
