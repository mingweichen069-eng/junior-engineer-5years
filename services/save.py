"""存档：SQLite 单文件，支持多槽位。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from core.model.serde import from_jsonable, to_jsonable
from core.model.state import GameState

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "saves" / "game.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS saves (
    slot TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    state TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slot TEXT NOT NULL,
    week INTEGER NOT NULL,
    label TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS profile (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""


def _connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or DB_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


def save_game(slot: str, state: GameState, name: str = "自动存档", path: Path | None = None) -> None:
    payload = json.dumps(
        {
            "save_version": 1,
            "state_version": 1,
            "rules_version": state.meta.rules_version,
            "state": to_jsonable(state),
        },
        ensure_ascii=False,
    )
    with _connect(path) as conn:
        conn.execute(
            "INSERT INTO saves(slot, name, state, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(slot) DO UPDATE SET name=excluded.name, state=excluded.state, updated_at=excluded.updated_at",
            (slot, name, payload, datetime.now().isoformat(timespec="seconds")),
        )


def load_game(slot: str, path: Path | None = None) -> GameState | None:
    with _connect(path) as conn:
        row = conn.execute("SELECT state FROM saves WHERE slot=?", (slot,)).fetchone()
    if not row:
        return None
    data = json.loads(row[0])
    st = from_jsonable(GameState, data["state"])
    return _migrate(st)


def _migrate(st: GameState) -> GameState:
    """旧存档升级。

    v3→v4：行动力从 5 点制扩到 24 点制，科技进度按新刻度保留。
    v4→v5：科技改为随时间推进的时间读条 + 自学精力；精力给一个温和开局值。
    v6→v7：v1.0 取消读条 / 考核 / 解锁，改为三维（玩家知识 / 角色履历 / 公司资产）。
           旧档里的 unlocked / exams / progress 字段由 serde 直接忽略，
           assets / career_rec / entries 用默认值补齐——旧局按新规则继续跑。
    """
    if int(st.meta.save_version) < 4:
        from core.rules import actions as action_rules

        st.player.ap = action_rules.ap_cap(st)
    if int(st.meta.save_version) < 5:
        # 精力是新增资源：给老存档一个可用值，避免第一周只能 1 点龟速
        st.player.energy = max(float(st.player.energy), 50.0)
        st.meta.save_version = 5
        st.meta.rules_version = "0.8.0"
    if int(st.meta.save_version) < 6:
        # v0.9：长局 + 可选结算周 + 参考评价。老档给一个居中的目标周，
        # 已经结束的局不重新弹评价（should_review 会检查 ending.ended）。
        st.meta.save_version = 6
        st.meta.rules_version = "0.9.0"
        if not (50 <= int(st.meta.review_week) <= 100):
            st.meta.review_week = 60
    if int(st.meta.save_version) < 7:
        st.meta.save_version = 7
        st.meta.rules_version = "1.0.0"
    return st


def list_saves(path: Path | None = None) -> list[dict]:
    with _connect(path) as conn:
        rows = conn.execute("SELECT slot, name, updated_at FROM saves ORDER BY updated_at DESC").fetchall()
    return [{"slot": r[0], "name": r[1], "updated_at": r[2]} for r in rows]


# ---------------- 快照：每个动作前自动存档，可回退 ----------------

def save_snapshot(slot: str, state: GameState, label: str, path: Path | None = None) -> int:
    payload = json.dumps(to_jsonable(state), ensure_ascii=False)
    with _connect(path) as conn:
        cur = conn.execute(
            "INSERT INTO snapshots(slot, week, label, state, created_at) VALUES(?,?,?,?,?)",
            (slot, state.meta.week, label, payload, datetime.now().isoformat(timespec="seconds")),
        )
        return int(cur.lastrowid)


def list_snapshots(slot: str, limit: int = 30, path: Path | None = None) -> list[dict]:
    with _connect(path) as conn:
        rows = conn.execute(
            "SELECT id, week, label, created_at FROM snapshots WHERE slot=? ORDER BY id DESC LIMIT ?",
            (slot, limit),
        ).fetchall()
    return [{"id": r[0], "week": r[1], "label": r[2], "created_at": r[3]} for r in rows]


def load_snapshot(snapshot_id: int, path: Path | None = None) -> GameState | None:
    with _connect(path) as conn:
        row = conn.execute("SELECT state FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
    if not row:
        return None
    return from_jsonable(GameState, json.loads(row[0]))


# ---------------- 跨局档案：已解锁知识 / 设置 / 槽位管理 ----------------

DEFAULT_PROFILE = {
    # 玩家知识：跨所有存档永久保留。它是「你学会了什么」，不是角色等级，
    # 也不授予任何游戏内资格；知识留在玩家自己头脑里。
    "player_knowledge": [],     # 查阅过的知识卡 id
    "practiced_tech": [],       # 亲手实验 / 部署过的技术档案 id
    "solved_incidents": [],     # 解决过的重大事件类型
    "achievements": [],         # 历次评价拿到过的数据成就 id
    "best_rank": "",
    "reviews": 0,
    "settings": {
        "auto_ms": 1600,
        "show_tips": True,
        "default_difficulty": "normal",
        "default_review_week": 60,
    },
}


def get_profile(path: Path | None = None) -> dict:
    out = json.loads(json.dumps(DEFAULT_PROFILE))
    with _connect(path) as conn:
        for key, value in conn.execute("SELECT key, value FROM profile"):
            try:
                out[key] = json.loads(value)
            except (ValueError, TypeError):
                out[key] = value
    return out


def set_profile(data: dict, path: Path | None = None) -> dict:
    cur = get_profile(path)
    for key in DEFAULT_PROFILE:
        if key not in data:
            continue
        if key == "settings" and isinstance(data[key], dict):
            cur["settings"].update(data[key])
        else:
            cur[key] = data[key]
    with _connect(path) as conn:
        for key, value in cur.items():
            conn.execute(
                "INSERT INTO profile(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value, ensure_ascii=False)),
            )
    return cur


def sync_profile(st: GameState, path: Path | None = None) -> dict:
    """把「玩家本人学会了什么」并进跨局档案。无变化就不写库。

    只同步玩家知识（永久）与成就；角色履历、公司资产属于这一局，不跨局继承。
    """
    cur = get_profile(path)
    know = set(cur.get("player_knowledge") or [])
    practiced = set(cur.get("practiced_tech") or [])
    incidents = set(cur.get("solved_incidents") or [])
    ach = set(cur.get("achievements") or [])
    before = (len(know), len(practiced), len(incidents), len(ach))
    for key, ok in (st.tech.knowledge or {}).items():
        if ok:
            know.add(key)
    for key, e in (st.tech.entries or {}).items():
        if e.get("experimented") or e.get("deployed"):
            practiced.add(key)
    for r in st.career_rec.resolved_events or []:
        incidents.add(r.get("id", ""))
    for rep in st.review.reports or []:
        for a in rep.get("achievements", []):
            if a.get("earned"):
                ach.add(a["id"])
    if (len(know), len(practiced), len(incidents), len(ach)) == before:
        return cur
    cur["player_knowledge"] = sorted(know)
    cur["practiced_tech"] = sorted(practiced)
    cur["solved_incidents"] = sorted(i for i in incidents if i)
    cur["achievements"] = sorted(ach)
    cur["reviews"] = max(int(cur.get("reviews", 0)), len(st.review.reports or []))
    ranks = ["D", "C", "B", "A", "S"]
    for rep in st.review.reports or []:
        r = rep.get("rank", "")
        if ranks.index(r) > ranks.index(cur.get("best_rank") or "D"):
            cur["best_rank"] = r
    return set_profile(cur, path)


def slot_details(path: Path | None = None) -> list[dict]:
    """槽位列表带上周数 / 难度 / 版本 / 结局，供主界面「加载游戏」用。"""
    with _connect(path) as conn:
        rows = conn.execute("SELECT slot, name, state, updated_at FROM saves ORDER BY updated_at DESC").fetchall()
    out = []
    for slot, name, raw, updated in rows:
        try:
            s = json.loads(raw).get("state", {})
            m = s.get("meta", {}) or {}
            week = int(m.get("week", 0))
            diff = m.get("difficulty", "")
            rv = m.get("rules_version", "")
            sandbox = bool(m.get("sandbox"))
            unlocked = sum(1 for v in (s.get("tech", {}).get("unlocked", {}) or {}).values() if v)
            ended = (s.get("ending", {}) or {}).get("ended")
        except (ValueError, TypeError, AttributeError):
            week, diff, rv, sandbox, unlocked, ended = 0, "", "", False, 0, None
        out.append({
            "slot": slot, "name": name, "updated_at": updated, "week": week,
            "difficulty": diff, "rules_version": rv, "sandbox": sandbox,
            "unlocked": unlocked, "ended": ended,
        })
    return out


def delete_slot(slot: str, path: Path | None = None) -> None:
    with _connect(path) as conn:
        conn.execute("DELETE FROM saves WHERE slot=?", (slot,))
        conn.execute("DELETE FROM snapshots WHERE slot=?", (slot,))


def rename_slot(slot: str, name: str, path: Path | None = None) -> None:
    with _connect(path) as conn:
        conn.execute("UPDATE saves SET name=? WHERE slot=?", (name, slot))
