"""v1.1.2 阶段六回归：技能不是等级，是工程经验。

任务书第七节要求：
- 清理 SKILL_GAIN_BY_ACTION / requires.skill / study_* / 旧技能经验逻辑与旧注释；
- 不简单删除角色技能，而是让它真实影响诊断、调查时间、实施效率、
  失误概率、事故风险、团队信任与职业评价；
- 不影响任何模拟结果的数值不再作为独立成长值保留；
- 经验来自处理真实工程问题，不是另一套科技解锁等级。

本文件覆盖：门槛清除、经验对风险/调查消耗/绩效的真实影响、
经验只从真实动手沉淀、沙盘不计入履历、旧注释与死数据清理。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.engine import new_game  # noqa: E402
from core.rules import actions as ar  # noqa: E402
from core.rules import career  # noqa: E402
from core.rules import workbench  # noqa: E402

GOOD_INDEX = "CREATE INDEX ix_exp ON orders(user_id, created_at)"


@pytest.fixture(autouse=True)
def isolated_lab(tmp_path, monkeypatch):
    monkeypatch.setattr(workbench, "LAB_DB", tmp_path / "lab.db")
    monkeypatch.setattr(workbench, "_roll_incident", lambda *a, **k: False)


def _rich(seed: int = 601, *, skills: float | None = None):
    st = new_game(seed=seed)
    st.player.ap = 999
    st.player.energy = 999.0
    if skills is not None:
        st.player.skills = {k: float(skills) for k in st.player.skills}
    return st


# ---------- 1. 旧成长机制与门槛的残留清理 ----------

def test_old_skill_growth_and_gates_are_gone():
    src = (ROOT / "core" / "rules" / "actions.py").read_text(encoding="utf-8")
    for dead in ("SKILL_GAIN_BY_ACTION", "SKILL_GAIN =", "_gain_skills",
                 "study_sql", "study_db", "study_ops"):
        assert dead not in src, dead
    assert "for skill, need in" not in src        # requires.skill 门槛循环
    assert '.get("skill"' not in src

    acts = json.loads((ROOT / "content" / "actions.json").read_text(encoding="utf-8"))
    items = acts if isinstance(acts, list) else acts.get("actions", [])
    assert all(not (a.get("requires") or {}).get("skill") for a in items)
    assert not [a for a in items if a.get("id", "").startswith("study_")]

    tech = (ROOT / "content" / "tech.json").read_text(encoding="utf-8")
    assert '"skill"' not in tech          # 8 处 effects.skill 死数据已清掉

    state_src = (ROOT / "core" / "model" / "state.py").read_text(encoding="utf-8")
    assert "动作门槛与考核用" not in state_src
    assert "自学精力回复" not in state_src
    js = (ROOT / "web" / "static" / "app.js").read_text(encoding="utf-8")
    assert "需要科技" not in js


def test_skills_never_gate_any_action():
    st = _rich(skills=0.0)
    st.meta.sandbox = True
    st.player.ap = 999
    st.company.cash_cents = 10 ** 9
    for a in ar.load_actions():
        ok, why = ar.check(st, a)
        assert ok, f"{a['id']} 被技能/其它门槛挡住了：{why}"


# ---------- 2. 经验真实影响模拟（而不是只显示一个数） ----------

def test_experience_reduces_change_risk_but_not_unverified_penalty():
    low = _rich(skills=0.0)
    high = _rich(skills=100.0)
    k = workbench._signature("index", GOOD_INDEX, workbench.parse_index(GOOD_INDEX))
    rl = workbench.risk_of(low, "index", k)
    rh = workbench.risk_of(high, "index", k)
    assert rh["total"] < rl["total"]
    assert rh["base"] < rl["base"]
    assert rh["experience_factor"] < rl["experience_factor"]
    # 客观风险不打折：没实验过就是没实验过，连续上线就是连续上线
    assert rh["untested"] == rl["untested"]
    assert rh["consecutive"] == rl["consecutive"]


def test_experience_reduces_survey_energy_cost():
    low = _rich(skills=0.0)
    high = _rich(skills=100.0)
    assert workbench._survey_energy(high, 4.0) < workbench._survey_energy(low, 4.0)

    e0 = low.player.energy
    workbench.experiment(low, "SELECT * FROM orders LIMIT 5")
    spent_low = e0 - low.player.energy
    e1 = high.player.energy
    workbench.experiment(high, "SELECT * FROM orders LIMIT 5")
    spent_high = e1 - high.player.energy
    assert spent_high < spent_low


def test_experience_affects_career_evaluation():
    low = _rich(skills=0.0)
    high = _rich(skills=100.0)
    assert career.skill_level(high) > career.skill_level(low)
    assert career.performance_score(high, 0.99) > career.performance_score(low, 0.99)
    # 只是 10% 权重，不能让经验单独把可用性救回来
    assert career.performance_score(high, 0.50) < career.performance_score(high, 0.99)


# ---------- 3. 经验只从真实动手沉淀 ----------

def test_successful_deploy_grows_experience():
    st = _rich()
    before = dict(st.player.skills)
    r = workbench.deploy_index(st, GOOD_INDEX)
    assert r["ok"]
    assert st.player.skills["db"] > before["db"]
    assert st.player.skills["sql"] > before["sql"]


def test_failed_deploy_gives_a_little_troubleshooting_experience():
    st = _rich()
    before = dict(st.player.skills)
    r = workbench.deploy_index(st, "nonsense")
    assert r["ok"] is False and r["charged"] is True
    assert st.player.skills["db"] > before["db"]
    assert st.player.skills["db"] - before["db"] < 0.5   # 比成功上线少得多


def test_reading_cards_or_marking_archive_does_not_grind_experience():
    st = _rich()
    before = dict(st.player.skills)
    workbench.mark(st, "db_t1", "seen")
    workbench.read_card(st, "some_card")
    assert st.player.skills == before


def test_sandbox_does_not_write_experience_into_career():
    st = _rich()
    st.meta.sandbox = True
    before = dict(st.player.skills)
    workbench.deploy_index(st, GOOD_INDEX)
    assert st.player.skills == before


def test_rewrite_verification_is_real_work_too():
    st = _rich()
    before = st.player.skills["sql"]
    r = workbench.check_rewrite_for_client(st, "q_dashboard",
                                            workbench.REFERENCE_REWRITES["q_dashboard"])
    assert r["ok"] and r["verified"] is True
    assert st.player.skills["sql"] > before
