from __future__ import annotations

import sys

import pytest


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Flask 测试客户端，存档写到临时目录。"""
    root = str(__import__("pathlib").Path(__file__).resolve().parents[1])
    if root not in sys.path:
        sys.path.insert(0, root)
    from services import save as saves

    monkeypatch.setattr(saves, "DB_PATH", tmp_path / "t.db")
    import web.app as webapp

    monkeypatch.setattr(webapp, "CURRENT_SLOT", "t")
    app = webapp.create_app()
    app.testing = True
    return app.test_client()


def finish_study(st, node_id: str, max_ticks: int = 40):
    """测试捷径：开始读条，然后靠每周 tick 把条走满（不花 AP、不改行动力）。"""
    from core.rules import tech

    node = tech.node_def(tech.load(), node_id)
    ok, msg = tech.toggle_study(st, node_id)
    assert ok, msg
    cost = tech.study_cost(st, node)
    for _ in range(max_ticks):
        tech.tick_study(st)
        if (
            st.tech.unlocked.get(node_id)
            or st.tech.exams.get(node_id)
            or int(st.tech.progress.get(node_id, 0)) >= cost
        ):
            break
    return st.tech.progress.get(node_id, 0)


def finish_msg(st, node_id: str):
    """返回 (ok, 文案)，文案里带「考核」或「已掌握」，供断言用。"""
    from core.rules import tech

    ok, msg = tech.toggle_study(st, node_id)
    assert ok, msg
    last = ""
    for _ in range(60):
        last = " ".join(tech.tick_study(st))
        if st.tech.unlocked.get(node_id) or st.tech.exams.get(node_id):
            break
    return True, last or "读条完成"


def pass_exam(st, node_id: str, weeks: int = 0):
    """测试捷径：把读条走满 + 用题库里的参考解通过考核。"""
    from core.rules import exam as exam_rules
    from core.rules import tech

    node = tech.node_def(tech.load(), node_id)
    st.tech.unlocked.update({r: True for r in node.get("requires_all", [])})
    finish_study(st, node_id)
    for _ in range(weeks):
        st.meta.week += 1
    task = exam_rules.task_for(node, st)
    if task is None:
        return True, "免考核"
    return tech.submit_exam(st, node_id, task.get("solution", ""))[:2]


@pytest.fixture()
def exam_pass():
    return pass_exam


@pytest.fixture()
def study_done():
    return finish_study


@pytest.fixture()
def study_finish():
    return finish_msg
