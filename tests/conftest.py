"""测试夹具。

V1.0 起没有读条 / 考核 / 解锁。旧夹具名保留，但语义改成
「把这项技术真的部署成公司资产」——因为数值修正现在只读资产。
"""

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


def deploy(st, node_id: str, max_ticks: int = 40):
    """测试捷径：把节点对应的设施直接部署到公司资产（等价于玩家在工作台动手）。"""
    from core.rules import workbench

    if node_id == "db_t2":
        workbench.deploy_rewrite(st, "q_dashboard")
        workbench.deploy_index(st, "CREATE INDEX ix_t2 ON orders(created_at)")
    elif node_id in ("db_replica", "db_read_replica"):
        st.assets.facilities["replica"] = st.assets.facilities.get("replica", 0) + 1
        st.app.read_replicas = min(3, st.app.read_replicas + 1)
    elif node_id in ("be_api", "circuit_breaker"):
        st.assets.facilities["circuit_breaker"] = 1
    elif node_id in ("be_cache", "cache"):
        st.assets.facilities["cache"] = 1
    else:
        workbench.deploy_index(st, "CREATE INDEX ix_fixture ON orders(user_id, created_at)")
    workbench.mark(st, node_id, "deployed")
    return True


def finish_study(st, node_id: str, max_ticks: int = 40):
    """兼容旧名：V1.0 里「读满」等价于「已部署」。"""
    deploy(st, node_id)
    return 1


def finish_msg(st, node_id: str):
    deploy(st, node_id)
    return True, "已部署到生产"


def pass_exam(st, node_id: str, weeks: int = 0):
    """兼容旧名：V1.0 没有考试，用「真的部署」替代。"""
    deploy(st, node_id)
    for _ in range(weeks):
        st.meta.week += 1
    return True, "已部署到生产"


@pytest.fixture()
def exam_pass():
    return pass_exam


@pytest.fixture()
def study_done():
    return finish_study


@pytest.fixture()
def study_finish():
    return finish_msg
